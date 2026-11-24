"""檔案傳送（Q94）的資料表、檔案庫與稽核紀錄。

## 資料表

  ft_file         檔案庫（221 上的檔案；內容放磁碟、這裡記名稱／大小／SHA256）
  ft_job          一次傳送（上傳或抓回）；背景執行，進度寫在這裡，關掉瀏覽器也不會斷
  ft_job_host     一次傳送裡「每一台」的結果
  ft_audit        稽核：模組開關、檔案庫異動、建立／停止傳送、重新驗證…（只增不改不刪）
  ft_cmd_log      指令執行紀錄：誰、何時、哪些主機、什麼指令（只增不改不刪）
  ft_cmd_result   指令的逐台輸出（只增不改不刪；一台一列）

## 「只增不改不刪」是怎麼保證的

不只是沒有刪除的 API——資料表本身掛了 BEFORE UPDATE／BEFORE DELETE 觸發器，直接下 SQL 也會被擋。
（要清理舊紀錄是日後另案、要明確改程式，不是順手一個 DELETE。）

## 檔案庫的容量

221 的磁碟只剩約 7 GB：總量預設上限 3 GB、單檔預設 1 GB（系統設定可調）、保留 30 天自動清除。
正在被傳送（排隊／等待時段／執行中）的檔案不會被自動清除，也不能手動刪。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import uuid
from pathlib import Path

import transfer_policy as pol
from transfer_redact import redact

MODULE_KEY = "file_transfer"

# app_settings 的鍵與預設值
SETTING_DEFAULTS = {
    "ft_library_cap_gb": "3",       # 檔案庫總量上限（GB）
    "ft_file_cap_gb": "1",          # 單一檔案上限（GB）
    "ft_retention_days": "30",      # 保留天數
    "ft_wave_pause_s": "120",       # 波次之間暫停幾秒
    "ft_max_active_jobs": "2",      # 同時進行（排隊或執行中）的傳送上限
    "ft_max_hosts_job": "300",      # 一次傳送最多幾台
    "ft_max_hosts_cmd": "100",      # 一次指令最多幾台
    "ft_max_waiting_jobs": "20",    # 同時「等待允許時段」的傳送上限
}

#: 檔案庫所在磁碟至少要留多少可用空間（221 的磁碟很小，不能被檔案庫塞滿）
DISK_RESERVE_BYTES = 1024 * 1024 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS ft_file (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'upload',      -- upload＝人上傳到檔案庫；pull＝從主機抓回
    from_host TEXT,
    from_path TEXT,
    uploaded_by TEXT,
    uploaded_at TEXT NOT NULL,
    deleted_at TEXT,
    deleted_by TEXT,
    delete_reason TEXT                           -- manual／retention
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ft_file_name_live ON ft_file(name) WHERE deleted_at IS NULL;
CREATE TABLE IF NOT EXISTS ft_job (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                          -- upload／download
    box TEXT NOT NULL,                           -- windows／linux（linux 箱含 AIX）
    file_id INTEGER,
    file_name TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    dest_folder TEXT NOT NULL,
    status TEXT NOT NULL,                        -- queued／running／waiting_window／done／partial／failed／stopped
    created_by TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    not_before TEXT,                             -- 等待允許時段：最早什麼時候自動開始
    concurrency INTEGER NOT NULL DEFAULT 2,
    rate_mbps INTEGER NOT NULL DEFAULT 5,
    overwrite INTEGER NOT NULL DEFAULT 0,
    stop_requested INTEGER NOT NULL DEFAULT 0,
    rooms INTEGER,
    est_minutes INTEGER,
    note TEXT
);
CREATE TABLE IF NOT EXISTS ft_job_host (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    ip TEXT NOT NULL,
    asset_serial TEXT,
    hostname TEXT,
    platform TEXT,
    room TEXT,
    status TEXT NOT NULL,                        -- queued／running／ok／failed／stopped
    message TEXT,
    pct INTEGER NOT NULL DEFAULT 0,
    remote_path TEXT,
    remote_sha256 TEXT,
    started_at TEXT,
    finished_at TEXT,
    verify_state TEXT,                           -- exists／changed／gone／error（重新驗證的結果）
    verify_detail TEXT,
    verified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ft_job_host_job ON ft_job_host(job_id, seq);
CREATE TABLE IF NOT EXISTS ft_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    by_user TEXT,
    action TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS ft_cmd_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    by_user TEXT,
    box TEXT NOT NULL,
    host_count INTEGER NOT NULL,
    hosts_json TEXT NOT NULL,                    -- [{ip, hostname, folder}]
    command TEXT NOT NULL,                       -- 已遮蔽機密後的文字
    redacted INTEGER NOT NULL DEFAULT 0,         -- 指令裡遮了幾處
    cmd_sha256 TEXT                              -- 「遮蔽前」原始指令的 SHA256（只能用來比對，還原不出原文；舊紀錄為空）
);
CREATE TABLE IF NOT EXISTS ft_cmd_result (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id INTEGER NOT NULL,
    at TEXT NOT NULL,
    ip TEXT NOT NULL,
    hostname TEXT,
    folder TEXT,
    ok INTEGER NOT NULL,
    rc INTEGER,
    output TEXT,
    truncated INTEGER NOT NULL DEFAULT 0,
    redacted INTEGER NOT NULL DEFAULT 0          -- 輸出裡遮了幾處
);
CREATE INDEX IF NOT EXISTS idx_ft_cmd_result_log ON ft_cmd_result(log_id);
"""

#: 只增不改不刪的表
APPEND_ONLY = ("ft_audit", "ft_cmd_log", "ft_cmd_result")

#: 指令輸出每台最多存多少字元（超過截斷並標記，避免一個 `cat 大檔` 把資料庫撐爆）
CMD_OUTPUT_KEEP = 20000


def _trigger_sql(table: str) -> str:
    return (
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} 是只增不改的稽核紀錄'); END;\n"
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} 是只增不刪的稽核紀錄'); END;\n"
        # INSERT OR REPLACE／REPLACE INTO 會先刪掉同 id 的舊列再插入（刪除觸發器在預設設定下不會被喚醒）：
        # 所以插入前就擋——指定了已存在的 id 一律中止（審查 MEDIUM 5）。
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} "
        f"WHEN NEW.id IS NOT NULL AND EXISTS (SELECT 1 FROM {table} WHERE id = NEW.id) "
        f"BEGIN SELECT RAISE(ABORT, '{table} 是只增不改的稽核紀錄（不可覆蓋）'); END;\n")


def is_admin(session) -> bool:
    """檔案傳送的 admin 判定：看 users.role（Q91，和選單管理同一個判斷；登入停用時另有規則，見 routers/transfer）。"""
    import auth

    return bool(session is not None and auth.is_admin(session))


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA recursive_triggers = ON")      # 連線層設定：REPLACE 的刪除動作也會喚醒刪除觸發器（雙保險）
    conn.executescript(SCHEMA)
    for t in ("ft_cmd_log", "ft_cmd_result"):         # 2.008.0 → 2.008.1 補欄（新模組，只有開發庫可能缺）
        if "redacted" not in {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}:
            conn.execute(f"ALTER TABLE {t} ADD COLUMN redacted INTEGER NOT NULL DEFAULT 0")
    if "cmd_sha256" not in {r[1] for r in conn.execute("PRAGMA table_info(ft_cmd_log)")}:
        conn.execute("ALTER TABLE ft_cmd_log ADD COLUMN cmd_sha256 TEXT")     # 舊庫補欄；舊列維持 NULL
    conn.executescript("".join(_trigger_sql(t) for t in APPEND_ONLY))
    conn.commit()


def now_str(t: _dt.datetime | None = None) -> str:
    return (t or pol.now()).strftime("%Y-%m-%d %H:%M:%S")


def parse_ts(s: str | None) -> _dt.datetime | None:
    if not s:
        return None
    try:
        return _dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=pol.TZ)
    except ValueError:
        return None


# ---------------------------------------------------------------- 設定
def get_settings(conn: sqlite3.Connection) -> dict:
    out = {}
    for k, d in SETTING_DEFAULTS.items():
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (k,)).fetchone()
        v = (row["value"] if row else None) or d
        try:
            f = float(v)
        except ValueError:
            f = float(d)
        out[k] = int(f) if f == int(f) else f
    return out


def set_settings(conn: sqlite3.Connection, changes: dict) -> dict:
    """只收白名單裡的鍵，值必須是正數。"""
    for k, v in changes.items():
        if k not in SETTING_DEFAULTS:
            raise ValueError(f"不認得的設定：{k}")
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{k} 必須是數字") from None
        if f <= 0 or f > 1000:
            raise ValueError(f"{k} 必須大於 0 且不超過 1000")
        txt = str(int(f)) if f == int(f) else repr(f)
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, txt))
    conn.commit()
    return get_settings(conn)


# ---------------------------------------------------------------- 稽核
def audit(conn: sqlite3.Connection, user: str | None, action: str, detail: str = "") -> None:
    import audit_digest

    detail = audit_digest.tag_if_login_disabled(conn, detail or "")      # 登入停用時的匿名操作一眼可辨
    conn.execute("INSERT INTO ft_audit (at, by_user, action, detail) VALUES (?,?,?,?)",
                 (now_str(), user, action, (detail or "")[:2000]))
    conn.commit()


def list_audit(conn: sqlite3.Connection, limit: int = 500) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, at, by_user, action, detail FROM ft_audit ORDER BY id DESC LIMIT ?", (limit,))]


# ---------------------------------------------------------------- 檔案庫
def _db_file(conn: sqlite3.Connection) -> Path:
    row = conn.execute("PRAGMA database_list").fetchone()
    return Path(row[2]) if row and row[2] else Path("asset.db")


def library_dir(conn: sqlite3.Connection) -> Path:
    env = os.environ.get("WEBIT3_TRANSFER_DIR", "").strip()
    d = Path(env) if env else _db_file(conn).parent / "transfer_library"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)                    # 檔案庫只有服務帳號讀得到（審查 LOW 10）
    except OSError:
        pass
    return d


def disk_room(conn: sqlite3.Connection) -> int:
    """檔案庫所在磁碟的實際可用空間，扣掉保留量（DISK_RESERVE_BYTES）。查不到就當 0（fail-closed）。"""
    try:
        return max(0, shutil.disk_usage(library_dir(conn)).free - DISK_RESERVE_BYTES)
    except OSError:
        return 0


def room_for_new_file(conn: sqlite3.Connection) -> tuple[int, dict]:
    """還能收進多少位元組：單檔上限、總量剩餘、磁碟實際剩餘（扣保留量）三者取最小。回 (room, info)。"""
    info = library_info(conn)
    return max(0, min(info["file_cap"], info["cap"] - info["used"], disk_room(conn))), info


def stored_path(conn: sqlite3.Connection, row) -> Path:
    # stored_name 是我們自己產的 uuid，不含路徑；再確認一次沒有目錄成分才拼路徑
    sn = row["stored_name"]
    if not re.fullmatch(r"[0-9a-f]{32}", sn):
        raise ValueError("檔案庫內部名稱不合法")
    return library_dir(conn) / sn


def _file_row(r) -> dict:
    d = dict(r)
    d.pop("stored_name", None)
    return d


def list_files(conn: sqlite3.Connection) -> list[dict]:
    return [_file_row(r) for r in conn.execute(
        "SELECT * FROM ft_file WHERE deleted_at IS NULL ORDER BY id")]


def get_file(conn: sqlite3.Connection, file_id: int):
    return conn.execute("SELECT * FROM ft_file WHERE id = ? AND deleted_at IS NULL", (file_id,)).fetchone()


def library_used(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COALESCE(SUM(size), 0) FROM ft_file WHERE deleted_at IS NULL").fetchone()[0]


def library_info(conn: sqlite3.Connection) -> dict:
    s = get_settings(conn)
    return {"used": library_used(conn), "cap": int(s["ft_library_cap_gb"] * pol.GB),
            "file_cap": int(s["ft_file_cap_gb"] * pol.GB), "retention_days": int(s["ft_retention_days"])}


class LibraryError(ValueError):
    """檔案庫規則不允許（訊息直接給人看）。status＝建議的 HTTP 狀態碼（太大＝413）。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


#: 進行中的上傳已「預留」的位元組（全行程共用）。N 條上傳同時進來時，每一條都要在「單檔／總量剩餘／磁碟剩餘（扣保留量）」
#: 減掉別人已預留的之後還放得下，才收得進來——不會各自看到同一份剩餘空間而一起超過上限（審查 M3）。
_reserve_lock = threading.Lock()
_reserved = [0]


class UploadSink:
    """收一份上傳檔進檔案庫（審查 HIGH 4／M3）：上限＝「單檔上限、總量剩餘、磁碟實際剩餘（扣 1 GB 保留）」三者最小值，
    再扣掉其他進行中上傳已預留的量。宣告的大小一超過就在讀任何內容之前拒絕；寫入途中一超過就中止並刪暫存。
    檔案 0600、資料夾 0700。檔名唯一由資料庫的唯一索引保證（同名搶著收，後到的 409）。"""

    def __init__(self, conn: sqlite3.Connection, name: str, declared: int | None = None):
        if not name or len(name) > 128 or "/" in name or "\\" in name:
            raise LibraryError("檔名不合法")
        if conn.execute("SELECT 1 FROM ft_file WHERE name = ? AND deleted_at IS NULL", (name,)).fetchone():
            raise LibraryError(f"檔案庫已經有「{name}」，請先刪除舊的或改名", 409)
        self.conn, self.name = conn, name
        self.reserved = 0
        self.size = 0
        self._h = hashlib.sha256()
        self._f = None
        with _reserve_lock:
            self.base_room, self.info = room_for_new_file(conn)
            avail = self.base_room - _reserved[0]
            if avail <= 0:
                raise LibraryError("檔案庫空間不足（含磁碟剩餘空間保留量與其他進行中的上傳）", 413)
            if declared is not None:
                if declared > avail:
                    raise LibraryError(f"檔案太大：上限 {pol.fmt_size(avail)}（單檔、總量、磁碟剩餘三者取最小，已扣其他進行中的上傳）", 413)
                self.reserved = declared
                _reserved[0] += declared
        try:
            self.sn = uuid.uuid4().hex
            self.dest = library_dir(conn) / self.sn
            self._f = os.fdopen(os.open(self.dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")
        except BaseException:
            self._release()
            raise

    def _release(self) -> None:
        with _reserve_lock:
            _reserved[0] = max(0, _reserved[0] - self.reserved)
            self.reserved = 0

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        too_big, others = False, 0
        with _reserve_lock:
            if self.size > self.reserved:
                others = _reserved[0] - self.reserved
                if self.size > self.base_room - others:
                    too_big = True
                else:
                    too_big = False
                    _reserved[0] += self.size - self.reserved
                    self.reserved = self.size
        if too_big:
            self.abort()
            raise LibraryError(f"檔案太大：上限 {pol.fmt_size(max(0, self.base_room - others))}（單檔、總量、磁碟剩餘三者取最小，已扣其他進行中的上傳）", 413)
        self._h.update(chunk)
        self._f.write(chunk)

    def abort(self) -> None:
        try:
            if self._f is not None:
                self._f.close()
        finally:
            self.dest.unlink(missing_ok=True)
            self._release()

    def commit(self, user: str | None, source: str = "upload", from_host: str | None = None,
               from_path: str | None = None) -> dict:
        try:
            self._f.close()
            if self.size == 0:
                raise LibraryError("檔案是空的")
            info = library_info(self.conn)                      # 寫的期間別人可能已經收了檔案：commit 前用最新用量再驗一次
            if self.size > info["file_cap"] or info["used"] + self.size > info["cap"]:
                raise LibraryError("檔案庫空間不足（上傳期間其他檔案已用掉空間）", 413)
            try:
                cur = self.conn.execute(
                    "INSERT INTO ft_file (name, stored_name, size, sha256, source, from_host, from_path, uploaded_by, uploaded_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (self.name, self.sn, self.size, self._h.hexdigest(), source, from_host, from_path, user, now_str()))
                self.conn.commit()
            except sqlite3.IntegrityError as exc:
                self.conn.rollback()
                raise LibraryError(f"檔案庫已經有「{self.name}」，請先刪除舊的或改名", 409) from exc
            return _file_row(self.conn.execute("SELECT * FROM ft_file WHERE id = ?", (cur.lastrowid,)).fetchone())
        except BaseException:
            self.dest.unlink(missing_ok=True)                    # 任何原因 INSERT 沒成功：磁碟上的檔案一定要刪掉，不留孤兒
            raise
        finally:
            self._release()


def add_from_stream(conn: sqlite3.Connection, name: str, stream, user: str | None,
                    source: str = "upload", from_host: str | None = None, from_path: str | None = None) -> dict:
    """同步版（測試與內部用）：從有 read() 的物件收進檔案庫。"""
    sink = UploadSink(conn, name)
    try:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            sink.write(chunk)
    except BaseException:
        sink.abort()
        raise
    return sink.commit(user, source, from_host, from_path)


def adopt_local_file(conn: sqlite3.Connection, name: str, tmp_path: Path, sha256: str, size: int,
                     user: str | None, from_host: str, from_path: str) -> dict:
    """抓回來的檔案：已經在本機暫存、雜湊算好了，這裡做容量檢查後搬進檔案庫。"""
    room, info = room_for_new_file(conn)
    try:
        if size > info["file_cap"]:
            raise LibraryError(f"超過單一檔案上限 {pol.fmt_size(info['file_cap'])}")
        if size > room:
            raise LibraryError(f"檔案庫空間不足（總量上限 {pol.fmt_size(info['cap'])}，已用 {pol.fmt_size(info['used'])}，或磁碟剩餘不足）")
        if conn.execute("SELECT 1 FROM ft_file WHERE name = ? AND deleted_at IS NULL", (name,)).fetchone():
            raise LibraryError(f"檔案庫已經有「{name}」，請先刪除舊的或改名", 409)
        sn = uuid.uuid4().hex
        os.replace(tmp_path, library_dir(conn) / sn)
        try:
            os.chmod(library_dir(conn) / sn, 0o600)
        except OSError:
            pass
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    try:
        cur = conn.execute(
            "INSERT INTO ft_file (name, stored_name, size, sha256, source, from_host, from_path, uploaded_by, uploaded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)", (name, sn, size, sha256, "pull", from_host, from_path, user, now_str()))
        conn.commit()
    except BaseException as exc:
        (library_dir(conn) / sn).unlink(missing_ok=True)               # INSERT 沒成功就不留孤兒檔
        if isinstance(exc, sqlite3.IntegrityError):
            conn.rollback()
            raise LibraryError(f"檔案庫已經有「{name}」，請先刪除舊的或改名", 409) from exc
        raise
    return _file_row(conn.execute("SELECT * FROM ft_file WHERE id = ?", (cur.lastrowid,)).fetchone())


ACTIVE_STATUSES = ("queued", "running", "waiting_window")


def file_in_use(conn: sqlite3.Connection, file_id: int) -> bool:
    ph = ",".join("?" for _ in ACTIVE_STATUSES)
    return conn.execute(f"SELECT 1 FROM ft_job WHERE file_id = ? AND status IN ({ph}) LIMIT 1",
                        (file_id, *ACTIVE_STATUSES)).fetchone() is not None


def delete_file(conn: sqlite3.Connection, file_id: int, user: str | None, reason: str = "manual") -> bool:
    row = get_file(conn, file_id)
    if row is None:
        return False
    if file_in_use(conn, file_id):
        raise LibraryError("這個檔案正在排隊或傳送中，不能刪")
    try:
        stored_path(conn, row).unlink(missing_ok=True)
    except OSError as exc:
        raise LibraryError(f"刪除檔案失敗：{exc}") from exc
    conn.execute("UPDATE ft_file SET deleted_at = ?, deleted_by = ?, delete_reason = ? WHERE id = ?",
                 (now_str(), user, reason, file_id))
    conn.commit()
    audit(conn, user, "library_delete" if reason == "manual" else "library_retention",
          f"{row['name']}（{row['size']} 位元組）")
    return True


def purge_expired(conn: sqlite3.Connection, now: _dt.datetime | None = None) -> int:
    """超過保留天數的檔案自動清除；有傳送在用的先留著。回清掉幾個。"""
    days = get_settings(conn)["ft_retention_days"]
    cutoff = (now or pol.now()) - _dt.timedelta(days=days)
    n = 0
    for r in conn.execute("SELECT id, uploaded_at FROM ft_file WHERE deleted_at IS NULL").fetchall():
        t = parse_ts(r["uploaded_at"])
        if t is not None and t < cutoff:
            try:
                if delete_file(conn, r["id"], "(系統)", "retention"):
                    n += 1
            except LibraryError:
                continue            # 還在被傳送用：下次再清
    return n


# ---------------------------------------------------------------- 指令紀錄
def cmd_log_start(conn: sqlite3.Connection, user: str | None, box: str, hosts: list[dict], command: str) -> int:
    safe, n = redact(command)                       # 紀錄不可刪，所以進紀錄之前就遮；實際執行的仍是原文
    cur = conn.execute(
        "INSERT INTO ft_cmd_log (at, by_user, box, host_count, hosts_json, command, redacted, cmd_sha256) VALUES (?,?,?,?,?,?,?,?)",
        (now_str(), user, box, len(hosts),
         json.dumps([{"ip": h["ip"], "hostname": h.get("hostname"), "folder": h.get("folder")} for h in hosts],
                    ensure_ascii=False), safe, n, hashlib.sha256(command.encode("utf-8")).hexdigest()))
    conn.commit()
    return cur.lastrowid


def cmd_log_result(conn: sqlite3.Connection, log_id: int, host: dict, ok: bool, rc: int | None, output: str) -> None:
    out, n = redact(output or "")
    trunc = 0
    if len(out) > CMD_OUTPUT_KEEP:
        out, trunc = out[:CMD_OUTPUT_KEEP], 1
    conn.execute(
        "INSERT INTO ft_cmd_result (log_id, at, ip, hostname, folder, ok, rc, output, truncated, redacted) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (log_id, now_str(), host["ip"], host.get("hostname"), host.get("folder"), 1 if ok else 0, rc, out, trunc, n))
    conn.commit()


def list_cmd_logs(conn: sqlite3.Connection, limit: int = 500) -> list[dict]:
    rows = conn.execute(
        "SELECT l.id, l.at, l.by_user, l.box, l.host_count, l.command, l.redacted, l.cmd_sha256, "
        "(SELECT COUNT(*) FROM ft_cmd_result r WHERE r.log_id = l.id) AS done, "
        "(SELECT COUNT(*) FROM ft_cmd_result r WHERE r.log_id = l.id AND r.ok = 1) AS ok_count "
        "FROM ft_cmd_log l ORDER BY l.id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_cmd_log(conn: sqlite3.Connection, log_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM ft_cmd_log WHERE id = ?", (log_id,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    d["hosts"] = json.loads(d.pop("hosts_json"))
    d["results"] = [dict(x) for x in conn.execute(
        "SELECT ip, hostname, folder, ok, rc, output, truncated, redacted, at FROM ft_cmd_result WHERE log_id = ? ORDER BY id",
        (log_id,))]
    d["done"] = len(d["results"])
    d["ok_count"] = sum(1 for x in d["results"] if x["ok"])
    d["redacted_total"] = d["redacted"] + sum(x["redacted"] for x in d["results"])      # 「已遮蔽 N 處」
    return d
