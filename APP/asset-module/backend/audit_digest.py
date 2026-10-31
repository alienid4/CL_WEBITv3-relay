"""稽核表的「外部摘要」（Codex 第三次審查第 7 點）。

只增不改的稽核表（ft_audit／ft_cmd_log／ft_cmd_result／export_log／access_level_audit／credential_use_audit）
原本只靠資料庫觸發器防竄改；能寫資料庫檔案的人可以先 DROP TRIGGER 再改紀錄。
這裡把每張表依 id 順序算一條 SHA256 雜湊鏈（h_n = sha256(h_{n-1} || canonical_json(row_n))），
定期把「時間、表名、筆數、最大 id、鏈尾雜湊」追加寫到資料庫資料夾旁的 audit_digest.log（不在資料庫裡）。
之後重算到當時留存的最大 id，對不上就知道哪張表、大約哪幾筆之間被動過。

- 每輪留存固定六張表各一行、固定順序（round 欄位相同）；驗證會檢查最後一輪是否六表齊全。
- log 每行自帶 prev（前一行的 h）與自己的 h，形成第二層鏈；中間被改、被插入都會斷。
- 留存是增量（從上一輪的 max_id／鏈尾往後只算新列）；完整重算只在 verify。
- canonical_json 排除值為 NULL 的欄位：之後替表加「可為空的新欄位」不會害舊紀錄的雜湊全部對不上。
- 這些表本來就不存密碼，摘要裡也只有雜湊與計數。
- 不改任何資料表結構。

已知限制（同一台機器上的 log 本質上的限制，不是 bug）：
  1. 能同時改資料庫與 audit_digest.log 的人，可以改完後重算整條鏈，驗證會照樣「一致」；
  2. 把整套舊資料庫＋舊 log 一起回放回去，也驗不出來；
  3. 整輪被從 log 結尾截掉驗不出來（只偵測「最後一輪表不齊」的部分截斷）。
  這三項都要靠「外部錨點」解：定期把 audit_digest.log（或最新一行的 h）複製到另一台機器／唯讀位置。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

TABLES = ("ft_audit", "ft_cmd_log", "ft_cmd_result", "export_log", "access_level_audit", "credential_use_audit")
GENESIS = "0" * 64
LOG_NAME = "audit_digest.log"
MIN_INTERVAL_S = 3600                    # 每小時最多留存一次
STALE_MIN_S = 600                        # 鎖檔至少 10 分鐘內不強奪
LOGIN_DISABLED_TAG = "[登入停用]"

_lock = threading.Lock()


# ---------------------------------------------------------------- 登入停用標記
def tag_if_login_disabled(conn: sqlite3.Connection, text: str | None) -> str | None:
    """登入停用狀態下寫入的稽核紀錄，摘要欄位前加「[登入停用]」，讓匿名操作一眼可辨。"""
    try:
        from _app_shared import auth_disabled
        disabled = auth_disabled(conn)
    except Exception:  # noqa: BLE001 - 查不出狀態就不加標記，不可以讓稽核寫入失敗
        return text
    if not disabled:
        return text
    t = text or ""
    return t if t.startswith(LOGIN_DISABLED_TAG) else f"{LOGIN_DISABLED_TAG}{t}"


# ---------------------------------------------------------------- 雜湊
def _canon(d: dict) -> str:
    return json.dumps({k: v for k, v in d.items() if v is not None}, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _step(prev: str, body: str) -> str:
    return hashlib.sha256((prev + body).encode("utf-8")).hexdigest()


def _tables_present(conn: sqlite3.Connection) -> list[str]:
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return [t for t in TABLES if t in have]


def _walk(conn: sqlite3.Connection, table: str, marks) -> tuple[dict, tuple[int, str, int]]:
    """完整重算（只有 verify 用）；回 ({mark: (筆數, 鏈雜湊)}＝「id ≤ mark」的狀態, (總筆數, 鏈尾, 最大id))。"""
    marks = sorted(set(marks))
    out: dict = {}
    mi = 0
    h, n, last = GENESIS, 0, 0
    cur = conn.execute(f'SELECT * FROM "{table}" ORDER BY id')
    cols = [d[0] for d in cur.description]
    for row in cur:
        d = dict(zip(cols, tuple(row)))
        rid = d["id"]
        while mi < len(marks) and marks[mi] < rid:
            out[marks[mi]] = (n, h)
            mi += 1
        h = _step(h, _canon(d))
        n += 1
        last = rid
    while mi < len(marks):
        out[marks[mi]] = (n, h)
        mi += 1
    return out, (n, h, last)


def _walk_from(conn: sqlite3.Connection, table: str, after_id: int, h: str, n: int) -> tuple[int, str, int]:
    """增量：從 (after_id, 鏈尾 h, 筆數 n) 往後只算 id 更大的新列。"""
    last = after_id
    cur = conn.execute(f'SELECT * FROM "{table}" WHERE id > ? ORDER BY id', (after_id,))
    cols = [d[0] for d in cur.description]
    for row in cur:
        d = dict(zip(cols, tuple(row)))
        h = _step(h, _canon(d))
        n += 1
        last = d["id"]
    return n, h, last


# ---------------------------------------------------------------- log 檔
def log_path_for(conn: sqlite3.Connection) -> Path | None:
    row = conn.execute("PRAGMA database_list").fetchone()
    if not row or not row[2]:
        return None                                        # 記憶體資料庫：沒有「旁邊」可放
    return Path(row[2]).parent / LOG_NAME


def _line_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k not in ("h", "_line")}
    return _step(entry.get("prev", ""), _canon(body))


def _is_symlinked(path: Path) -> bool:
    try:
        return path.is_symlink() or path.parent.is_symlink()
    except OSError:
        return True


def _read_log(path: Path | None) -> tuple[list[dict], dict | None]:
    """回 (可解析的項目, log 鏈問題或 None)。項目多帶 _line（第幾行）。"""
    if path is None or not path.exists():
        return [], None
    if _is_symlinked(path):
        return [], {"line": 0, "message": "audit_digest.log 或它所在的資料夾是 symbolic link（拒讀，疑似被導向別處）"}
    entries: list[dict] = []
    problem = None
    prev = GENESIS
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for no, raw in enumerate(f, 1):
            raw = raw.rstrip("\n")
            if not raw.strip():
                continue
            try:
                e = json.loads(raw)
                if not isinstance(e, dict):
                    raise ValueError("not an object")
            except ValueError:
                problem = problem or {"line": no, "message": f"第 {no} 行無法解析（被改過或寫壞）"}
                continue
            if problem is None:
                if e.get("prev") != prev:
                    problem = {"line": no, "message": f"第 {no} 行接不上前一行（中間有行被改、刪或插入）"}
                elif e.get("h") != _line_hash(e):
                    problem = {"line": no, "message": f"第 {no} 行內容與它自己的雜湊對不上（這行被改過）"}
            prev = e.get("h") or prev
            e["_line"] = no
            entries.append(e)
    return entries, problem


def _try_append_only(path: Path) -> None:
    """Linux 上盡量把 log 設成 append-only（chattr +a）；沒權限、不是 Linux、失敗都靜默略過。"""
    try:
        if os.name != "posix" or not shutil.which("chattr"):
            return
        subprocess.run(["chattr", "+a", str(path)], capture_output=True, timeout=5, check=False)
    except Exception:  # noqa: BLE001 - 這是加分，絕不可以讓留存失敗
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, pid)             # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            k.CloseHandle(h)
            return True
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False
    except Exception:  # noqa: BLE001 - 判斷不出來就當還活著（寧可多等，不強奪）
        return True


class _FileLock:
    """跨行程的鎖（O_EXCL 鎖檔，內容＝pid／時間／nonce）。

    stale 判定：鎖齡超過 10 分鐘「而且」持有者 pid 已不存在才接手；退出時只刪自己建的鎖。
    """

    def __init__(self, path: Path):
        self.p = Path(str(path) + ".lock")
        self.token = f"{os.getpid()} {time.time():.3f} {os.urandom(4).hex()}"
        self.owned = False

    def _try_create(self) -> bool:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(self.p, flags, 0o600)
        except FileExistsError:
            return False
        try:
            os.write(fd, self.token.encode())
        finally:
            os.close(fd)
        self.owned = True
        return True

    def _stale(self) -> bool:
        try:
            age = time.time() - self.p.stat().st_mtime
        except OSError:
            return False
        if age < STALE_MIN_S:
            return False
        try:
            pid = int(self.p.read_text(encoding="utf-8", errors="replace").split()[0])
        except (OSError, ValueError, IndexError):
            return True                                        # 內容壞掉且已超過 10 分鐘：只靠鎖齡
        return not _pid_alive(pid)

    def __enter__(self):
        end = time.time() + 5
        while True:
            if self._try_create():
                return self
            if self._stale():
                try:
                    self.p.unlink()
                except OSError:
                    pass
                continue
            if time.time() > end:
                raise TimeoutError("audit_digest.log 鎖被占用")
            time.sleep(0.05)

    def __exit__(self, *a):
        if not self.owned:
            return
        try:
            if self.p.read_text(encoding="utf-8", errors="replace") == self.token:
                self.p.unlink()
        except OSError:
            pass


def _fmt(t: _dt.datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S")


def _parse(s) -> _dt.datetime | None:
    try:
        return _dt.datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


# ---------------------------------------------------------------- 留存
def _last_state(entries: list[dict]) -> dict:
    """每張表最後一筆有效摘要：{table: (max_id, tail, count)}。"""
    out = {}
    for e in entries:
        t = e.get("table")
        if t in TABLES and isinstance(e.get("max_id"), int) and isinstance(e.get("count"), int) and e.get("tail"):
            out[t] = (e["max_id"], e["tail"], e["count"])
    return out


def _compute_round(conn: sqlite3.Connection, entries: list[dict], now: _dt.datetime) -> tuple[list[str], str]:
    """算出新一輪（六表固定順序各一行，增量）。回 (行們, 這輪接在哪一行的 h 後面)。"""
    state = _last_state(entries)
    present = set(_tables_present(conn))
    rnd = max([e["round"] for e in entries if isinstance(e.get("round"), int)] + [0]) + 1
    prev0 = (entries[-1].get("h") if entries else None) or GENESIS
    prev = prev0
    lines = []
    for t in TABLES:
        if t in present:
            mx, tail, cnt = state.get(t, (0, GENESIS, 0))
            n, h, last = _walk_from(conn, t, mx, tail, cnt)
        else:
            n, h, last = 0, GENESIS, 0
        e = {"at": _fmt(now), "round": rnd, "table": t, "count": n, "max_id": last, "tail": h, "prev": prev}
        e["h"] = _line_hash(e)
        prev = e["h"]
        lines.append(json.dumps(e, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return lines, prev0


def _hourly_blocks(entries: list[dict], now: _dt.datetime) -> dict | None:
    if entries:
        last_at = _parse(entries[-1].get("at"))
        if last_at and 0 <= (now - last_at).total_seconds() < MIN_INTERVAL_S:
            return {"done": False, "reason": "hourly", "last_at": entries[-1].get("at"),
                    "message": "距離上次留存不到一小時"}
    return None


def _append(path: Path, data: bytes) -> None:
    if _is_symlinked(path):
        msg = "audit_digest.log 或所在資料夾是 symbolic link，拒絕寫入"
        print(f"[audit_digest] {msg}：{path}")
        raise OSError(msg)
    new_file = not path.exists()
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            w = os.write(fd, view)
            view = view[w:]
    finally:
        os.close(fd)
    if new_file:
        _try_append_only(path)


def retain(conn: sqlite3.Connection, *, force: bool = False, now: _dt.datetime | None = None) -> dict:
    """追加一輪摘要。force=False 時距離上次留存不到一小時就不做。

    全表計算在拿鎖「之前」先做完；拿鎖後只重讀 log、確認沒人動過就 append，被動過就重算一次。
    """
    path = log_path_for(conn)
    if path is None:
        return {"done": False, "reason": "no_log_path", "message": "資料庫不是檔案，無法留存外部摘要"}
    now = now or _dt.datetime.now()
    if _is_symlinked(path):
        _append(path, b"")                                  # 拒寫並記錄
    with _lock:
        entries, _p = _read_log(path)
        if not force:
            blocked = _hourly_blocks(entries, now)
            if blocked:
                return blocked
        lines, prev0 = _compute_round(conn, entries, now)       # 鎖外先算
        with _FileLock(path):
            entries2, _p2 = _read_log(path)
            last2 = (entries2[-1].get("h") if entries2 else None) or GENESIS
            if last2 != prev0:                                   # 算的當中別人寫了：重算一次
                if not force:
                    blocked = _hourly_blocks(entries2, now)
                    if blocked:
                        return blocked
                lines, prev0 = _compute_round(conn, entries2, now)
            _append(path, ("\n".join(lines) + "\n").encode("utf-8"))
        return {"done": True, "at": _fmt(now), "tables": len(lines)}


# ---------------------------------------------------------------- 驗證
def verify(conn: sqlite3.Connection) -> dict:
    """status：consistent 一致／tampered 資料表對不上或最後一輪不齊／log_broken 只有 log 自己斷了／none 尚無留存（不是通過）。"""
    path = log_path_for(conn)
    entries, problem = _read_log(path)
    base = {"entries": len(entries), "issues": [], "log_ok": problem is None, "log_problem": problem,
            "last_retained_at": entries[-1].get("at") if entries else None}
    if not entries:
        return {**base, "status": "none", "ok": False,
                "message": "尚無留存的外部摘要" if problem is None else f"尚無可用的外部摘要；{problem['message']}"}
    present = set(_tables_present(conn))
    by_table: dict[str, list[dict]] = {}
    for e in entries:
        t = e.get("table")
        if isinstance(t, str) and t in TABLES and isinstance(e.get("max_id"), int):
            by_table.setdefault(t, []).append(e)
    issues = []
    # 最後一輪是否六表齊全（log 結尾被截掉一部分就會缺）
    last = entries[-1]
    rnd = last.get("round")
    if isinstance(rnd, int):
        last_round = [e for e in entries if e.get("round") == rnd]
    else:
        last_round = [e for e in entries if e.get("at") == last.get("at")]
    got = [e.get("table") for e in last_round]
    if got != list(TABLES):
        missing = [t for t in TABLES if t not in got]
        issues.append({"table": ",".join(missing) or "(順序)", "kind": "incomplete_round", "after_id": 0, "upto_id": 0,
                       "retained_at": last.get("at"),
                       "message": f"最後一輪留存的摘要不齊（缺：{'、'.join(missing) or '順序不對'}），摘要檔結尾可能被截掉"})
    unretained = []
    for t in TABLES:
        if t in present and t not in by_table:
            try:
                has = conn.execute(f'SELECT 1 FROM "{t}" LIMIT 1').fetchone() is not None
            except sqlite3.Error:
                has = False
            if has:
                unretained.append(t)
    for t, es in by_table.items():
        if t not in present:
            if (es[-1].get("count") or 0) > 0:
                issues.append({"table": t, "kind": "missing", "after_id": 0, "upto_id": es[-1]["max_id"],
                               "retained_at": es[-1].get("at"),
                               "message": f"{t}：資料表不見了（留存時有 {es[-1].get('count')} 筆）"})
            continue
        walked, _tot = _walk(conn, t, [e["max_id"] for e in es])
        prev_max = 0
        for e in es:
            n, h = walked[e["max_id"]]
            if n != e.get("count") or h != e.get("tail"):
                kind = "deleted" if n < (e.get("count") or 0) else "modified"
                lo = min(prev_max, e["max_id"]) + 1
                what = "有紀錄被刪" if kind == "deleted" else "內容被改"
                issues.append({"table": t, "kind": kind, "after_id": lo - 1, "upto_id": e["max_id"],
                               "retained_at": e.get("at"),
                               "message": (f"{t}：第 {lo}～{e['max_id']} 筆之間被動過（{what}；"
                                           f"對不上 {e.get('at')} 留存的摘要，當時 {e.get('count')} 筆、現在 {n} 筆）")})
                break
            prev_max = max(prev_max, e["max_id"])
    if issues:
        status, msg = "tampered", "不一致：" + "；".join(i["message"] for i in issues)
        if problem:
            msg += f"；摘要檔也斷了：{problem['message']}"
    elif unretained:
        status, msg = "none", f"尚未留存（不算通過）：{'、'.join(unretained)} 有資料但摘要檔裡完全沒有它的摘要"
    elif problem:
        status, msg = "log_broken", f"資料表與摘要相符，但摘要檔自己的鏈斷了：{problem['message']}"
    else:
        status, msg = "consistent", "一致"
    return {**base, "status": status, "ok": status == "consistent", "issues": issues, "message": msg,
            "unretained": unretained}


def verify_then_retain(conn: sqlite3.Connection, *, now: _dt.datetime | None = None) -> dict:
    """先驗證、再留存新摘要（每小時最多一次）。任一步失敗只回報、不丟例外。"""
    out: dict = {}
    try:
        out["verify"] = verify(conn)
    except Exception as exc:  # noqa: BLE001
        out["verify"] = {"status": "error", "ok": False, "issues": [], "message": f"驗證失敗：{type(exc).__name__}: {exc}"}
    try:
        out["retained"] = retain(conn, now=now)
    except Exception as exc:  # noqa: BLE001
        out["retained"] = {"done": False, "reason": "error", "message": f"留存失敗：{type(exc).__name__}: {exc}"}
    return out


def scheduled(conn: sqlite3.Connection, *, now: _dt.datetime | None = None) -> dict | None:
    """背景排程每分鐘呼叫：沒到每小時就什麼都不做（不重算）；到了才驗證＋留存。失敗只記 log，不丟例外。"""
    try:
        now = now or _dt.datetime.now()
        entries, _p = _read_log(log_path_for(conn))
        if _hourly_blocks(entries, now):
            return None
        out = verify_then_retain(conn, now=now)
        v = out.get("verify", {})
        if v.get("status") in ("tampered", "log_broken", "error"):
            print(f"[audit_digest] 警告：{v.get('message')}")
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"[audit_digest] 排程留存失敗（不影響服務）：{type(exc).__name__}: {exc}")
        return None
