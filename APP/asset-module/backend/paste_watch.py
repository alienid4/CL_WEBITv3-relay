"""「我沒有密碼：取得納管指令」之後：使用者在主機上跑完 webit3_onboard.sh，**系統自己發現、自己收**
（決策 ONBOARD-FLOW1，使用者 2026-10-18：跑完不需要再按任何按鈕）。

流程：
  1. 2-1 勾主機 →「取得納管指令」→ issue() 把這批 IP 記成「等待中」。
  2. 排程每分鐘（scan_service._paste_watch_tick）＋畫面輪詢時 check()：
     每台每 2 分鐘最多試一次收集帳號金鑰登入（唯讀 `hostname`），三天內有效。
  3. 連得上 → 補資產登記（沒有的話）→ root 金鑰試連（ROOTKEY1，連得上才記 root_key_at）→
     收 facts、標已納管 → 背景跑納管後六樣收集（post_onboard，跟網頁一鍵納管同一條）。
  4. 連不上 → 講得出是哪一種（B6）：網路不通／金鑰被拒（附可能原因）／來源 IP 與登記不符／
     設定被還原（之前連得上、現在被拒）／主機金鑰不符——**永遠不是只有「納管失敗」**。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

PENDING_DAYS = 3
RETRY_SECONDS = 120
MAX_PER_TICK = 20

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS onboard_paste_pending (
    ip           TEXT PRIMARY KEY,
    platform     TEXT,
    issued_at    TEXT NOT NULL,
    issued_by    TEXT,
    last_try_at  TEXT,
    detected_at  TEXT,
    state        TEXT DEFAULT 'waiting',   -- waiting / ok / network / key_rejected / source_ip / reverted / hostkey / other / expired
    reason       TEXT,
    hints        TEXT,                     -- JSON 陣列：可能原因
    root_key     INTEGER DEFAULT 0,
    asset_serial TEXT
)
"""

STATE_LABEL = {
    "waiting": "等待執行（系統每 2 分鐘自動試連）",
    "ok": "已納管（已自動開始收集）",
    "network": "網路不通（22 連不到）",
    "key_rejected": "金鑰被拒",
    "source_ip": "來源 IP 與登記不符",
    "reverted": "設定被還原（可能被組態管理工具覆蓋）",
    "hostkey": "主機金鑰不符",
    "other": "連不上（原因見說明）",
    "expired": "超過 3 天沒偵測到，停止自動試連",
}


def _ensure(conn) -> None:
    conn.execute(TABLE_SQL)


def _now() -> datetime:
    return datetime.now()


def _fmt(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S")


def issue(conn, ips: list[str], by: str | None, platform_of=None) -> list[str]:
    """把這批 IP 記成等待中（重發會重新計時、清掉上次的結果）。回實際登記的 IP。"""
    _ensure(conn)
    now = _fmt(_now())
    out = []
    for ip in [i.strip() for i in ips if i and i.strip()]:
        plat = platform_of(ip) if platform_of else None
        conn.execute(
            "INSERT INTO onboard_paste_pending (ip, platform, issued_at, issued_by, state) "
            "VALUES (?,?,?,?, 'waiting') ON CONFLICT(ip) DO UPDATE SET platform=excluded.platform, "
            "issued_at=excluded.issued_at, issued_by=excluded.issued_by, last_try_at=NULL, "
            "detected_at=NULL, state='waiting', reason=NULL, hints=NULL, root_key=0",
            (ip, plat, now, by))
        out.append(ip)
    conn.commit()
    return out


def status(conn, ips: list[str] | None = None) -> list[dict]:
    _ensure(conn)
    sql = "SELECT * FROM onboard_paste_pending"
    params: list = []
    if ips:
        sql += f" WHERE ip IN ({','.join('?' for _ in ips)})"
        params = list(ips)
    rows = []
    for r in conn.execute(sql + " ORDER BY issued_at DESC, ip", params):
        d = dict(r)
        d["state_label"] = STATE_LABEL.get(d["state"], d["state"])
        d["hints"] = json.loads(d["hints"]) if d.get("hints") else []
        rows.append(d)
    return rows


def _source_ip_to(host: str) -> str | None:
    """收集器連這台時實際用的來源 IP（UDP connect，不送封包）。"""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 22))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def diagnose(conn, ip: str, err: str | None, *, was_ok: bool, source_ip=None) -> dict:
    """試連失敗 → {state, reason, hints}（B6）。"""
    import onboard_engine
    import onboard_hostfix as hf

    c = hf.classify_collect_error(err)
    if c["kind"] == "network":
        return {"state": "network", "reason": c["text"], "hints": c["hints"]}
    if c["kind"] == "hostkey":
        return {"state": "hostkey", "reason": c["text"], "hints": c["hints"]}
    if c["kind"] == "key_rejected":
        src = source_ip(ip) if source_ip else _source_ip_to(ip)
        allowed = [onboard_engine.resolve_collector_ip(conn),
                   *onboard_engine.resolve_extra_collector_ips(conn)]
        if src and src not in allowed:
            return {"state": "source_ip", "reason": STATE_LABEL["source_ip"],
                    "hints": [f"收集器連這台的來源是 {src}，但腳本裡 from=／AllowUsers 放行的是 "
                              f"{'、'.join(a for a in allowed if a)}——到設定頁補上備援位址後重跑腳本"]}
        if was_ok:
            return {"state": "reverted", "reason": STATE_LABEL["reverted"],
                    "hints": ["之前連得上、現在被拒：authorized_keys／sshd_config／access.conf 可能被組態管理工具"
                              "（Ansible／Puppet／IBM 的基準設定）蓋回去了，重跑一次腳本並請維運把例外寫進他們的範本",
                              *c["hints"]]}
        return {"state": "key_rejected", "reason": c["text"], "hints": c["hints"]}
    return {"state": "other", "reason": c["text"], "hints": []}


def check(conn, ips: list[str] | None = None, force: bool = False, *,
          probe=None, root_probe=None, collect=None, post_start=None, source_ip=None,
          now: datetime | None = None) -> list[dict]:
    """對等待中的主機各試一次。注入點都是給測試用的（不碰真網路）。回這次有試的那幾台結果。"""
    import manage_state
    import root_key

    _ensure(conn)
    now = now or _now()
    probe = probe or (lambda ip, acct: manage_state.probe_collect(
        ip, manage_state.COLLECTOR_KEY_DEFAULT, account=acct))
    root_probe = root_probe or root_key.probe_root
    if collect is None:
        def collect(serial):
            return manage_state.collect_facts_into_assets(conn, only_serial=serial)
    if post_start is None:
        def post_start(serial, by):
            import post_onboard

            return post_onboard.start(None, serial, by=by)

    # 過期的先一次標掉（不佔試連名額）
    conn.execute("UPDATE onboard_paste_pending SET state='expired' "
                 "WHERE state NOT IN ('ok','expired') AND issued_at < ?",
                 (_fmt(now - timedelta(days=PENDING_DAYS)),))
    conn.commit()
    # ⚠️ 「2 分鐘內試過」要在 SQL 裡先濾掉再取前 N 台（2026-10-18 審查）：
    # 原本先取 20 台再跳過剛試過的，第 21 台以後永遠排不到，三天後直接過期。
    # 最久沒試（含從沒試過）的排最前面。
    sql = "SELECT * FROM onboard_paste_pending WHERE state NOT IN ('ok','expired')"
    params: list = []
    if not force:
        sql += " AND (last_try_at IS NULL OR last_try_at <= ?)"
        params.append(_fmt(now - timedelta(seconds=RETRY_SECONDS)))
    if ips:
        sql += f" AND ip IN ({','.join('?' for _ in ips)})"
        params += list(ips)
    sql += " ORDER BY (last_try_at IS NOT NULL), last_try_at, issued_at LIMIT ?"
    params.append(MAX_PER_TICK)
    rows = [dict(r) for r in conn.execute(sql, params)]
    done = []
    for r in rows:
        if not _claim(conn, r, now, force):
            continue          # 別的執行緒剛拿走這台（排程與手動同時跑）
        try:
            done.append(_try_one(conn, r, now, probe, root_probe, collect, post_start, source_ip))
        finally:
            with _LOCK:
                _BUSY.discard(r["ip"])
    conn.commit()
    return done


_LOCK = __import__("threading").Lock()
_BUSY: set[str] = set()
_TICK_RUNNING = __import__("threading").Event()


def _claim(conn, r, now, force) -> bool:
    """每台一把鎖＋先寫 last_try_at 再連：同一台不會被兩個執行緒同時試。"""
    with _LOCK:
        if r["ip"] in _BUSY:
            return False
        _BUSY.add(r["ip"])
    cur = conn.execute(
        "UPDATE onboard_paste_pending SET last_try_at=? WHERE ip=? "
        "AND (last_try_at IS ? OR last_try_at = ?)",
        (_fmt(now), r["ip"], r["last_try_at"], r["last_try_at"]))
    conn.commit()
    if cur.rowcount != 1 and not force:
        with _LOCK:
            _BUSY.discard(r["ip"])
        return False
    return True


def tick_in_background(db_path=None) -> bool:
    """排程每分鐘叫這支：在背景執行緒跑 check()，上一輪還沒跑完就不疊加。回有沒有啟動。
    SSH 試連不在任何 HTTP 請求裡跑（畫面輪詢只讀狀態）。"""
    import threading

    if _TICK_RUNNING.is_set():
        return False
    _TICK_RUNNING.set()

    def worker():
        from db import get_connection

        c = None
        try:
            c = get_connection(db_path)
            check(c)
        except Exception as exc:  # noqa: BLE001 - 背景執行緒不可把例外丟到沒人接的地方；留紀錄
            try:
                import collect_log

                if c is not None:
                    collect_log.record(c, "paste_watch", None, False,
                                       f"自動偵測這一輪失敗：{type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001 - 連紀錄都寫不進去（DB 壞了）就只能放棄
                pass
        finally:
            if c is not None:
                c.close()
            _TICK_RUNNING.clear()

    threading.Thread(target=worker, daemon=True).start()
    return True


def _try_one(conn, r, now, probe, root_probe, collect, post_start, source_ip) -> dict:
    import manage_state
    import root_key

    ip = r["ip"]
    plat = r.get("platform") or manage_state.collect_platform_of(conn, ip, None) or "linux"
    # 腳本建的是新名（舊名 webit3scan 已換掉），試連也用新名
    acct = manage_state.onboard_account(conn, plat)
    ok, err = probe(ip, acct)
    stamp = _fmt(now)
    if not ok:
        prev = conn.execute("SELECT MAX(collect_ok) AS c FROM hardware WHERE ip=?", (ip,)).fetchone()
        was_ok = bool(prev and prev["c"])
        d = diagnose(conn, ip, err, was_ok=was_ok, source_ip=source_ip)
        conn.execute("UPDATE onboard_paste_pending SET last_try_at=?, state=?, reason=?, hints=? WHERE ip=?",
                     (stamp, d["state"], d["reason"], json.dumps(d["hints"], ensure_ascii=False), ip))
        # 還在等使用者跑的那種（網路不通、金鑰被拒）照樣繼續試，直到過期
        return {"ip": ip, **d}

    # ---- 連得上：跟網頁一鍵納管成功之後同一套收尾 ----
    # 同 IP 多筆登記：挑現役、已納管那筆（全入口同一把尺；原本照序號字母排，會挑到停用的舊登記）
    found = manage_state.serial_for_ip(conn, ip)
    serial = found or f"AUTO-{ip}"
    if not found:
        # 環境／資產狀態不替它填（2026-10-20：只有 CIA 匯入寫，見 lifecycle.py）
        conn.execute("INSERT OR IGNORE INTO hardware (asset_serial, ip) VALUES (?, ?)", (serial, ip))
    rk = False
    if plat in root_key.ELIGIBLE_PLATFORMS:
        try:
            rk = bool(root_probe(ip))
        except Exception:  # noqa: BLE001 - root 金鑰試不到就當沒有，照收集帳號收
            rk = False
        root_key.mark_host(conn, ip, rk)
    # 上面已用新帳號連進去過（probe）：把這台的收集帳號記成新帳號，之後的收集才用對帳號
    import collect_account_migration as cam

    cam.settle_after_onboard(conn, ip, plat, _probe=lambda *_: (True, None))
    res = collect(serial) or {}
    if res.get("updated", 0) >= 1:
        manage_state.mark_collect_ok(conn, serial, stamp)
    conn.execute(
        "INSERT INTO onboard_audit (target_ip, platform, login_user, trigger, triggered_by, ok, stage, "
        "message, output) VALUES (?,?,?,?,?,?,?,?,?)",
        (ip, plat, None, "paste", r.get("issued_by"), 1, "verify",
         "主機上執行納管腳本後，系統自動偵測到收集帳號可登入" + ("；root 收集金鑰可用（ROOTKEY1）" if rk else ""),
         None))
    conn.execute("UPDATE onboard_paste_pending SET last_try_at=?, detected_at=?, state='ok', reason=NULL, "
                 "hints=NULL, root_key=?, asset_serial=? WHERE ip=?", (stamp, stamp, int(rk), serial, ip))
    conn.commit()
    post_start(serial, f"paste:{r.get('issued_by') or ''}")
    return {"ip": ip, "state": "ok", "root_key": rk, "asset_serial": serial}
