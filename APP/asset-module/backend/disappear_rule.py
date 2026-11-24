"""「異常消失」判定規則（2026-11-20 使用者定案，D3 口徑區；同日依 GPT 兩輪審查 + 裁判修正為 2.069.2 第二版）。

舊規則：本次 vs 上次兩次掃描比，前一次有、這次沒有就記「異常消失」。
正式機實測：某次掃描一個網段沒掃完整，整段 24 台一次被記成「消失」（含閘道）。

新規則（門檻都在 app_settings，非法值不靜默：用預設並在活動紀錄留一筆）：
  1. 「一次」＝一個日曆日（台北時間）。同一天手動補跑、單網段排程不會重複計次：
     同一天各批的「有掃到」取聯集（任何一批掃到就歸零），各網段取當天最新一個成功涵蓋它的批次。
  2. 連續 N 次（預設 5）完整掃描都沒掃到才算消失；中間掃到一次就歸零重算。
  3. 掃描範圍以 scan_coverage 為準（cidr＋ok）：
       · 該批沒有涵蓋的網段＝不在這次範圍，不累加、也不參與「縮減」判定；
       · 涵蓋但 ok=0（失敗）＝這次對該網段不算；
       · 不用 scan_history 的文字欄去反推失敗範圍（成功列寫 CIDR、失敗列歷史上寫顯示名稱，兩邊對不上）。
     沒有 scan_coverage 紀錄的舊批次才退回舊做法（用 scan_history 的 segment 欄）。
     segment 為空的列＝品質未知，不累加消失。
  4. 不完整批次不計入：網段台數比參考少超過 X%（預設 20%，網段原本至少 5 台）、或整批（扣掉已被網段解釋的缺口後）
     少超過 X%、或整批 0 台／全部網段失敗。台數＝distinct 主機身分（IP，無 IP 才用主機名），不是列數。
     參考基準只在「完整批次」或「連續 N 批縮減確認＝採新基準」時才更新；採新基準當下留事件（scan_batch_quality.new_baseline），
     畫面提示讓人看得到「系統自己接受了縮小」。
  5. 交叉確認：vCOps／RVTools（source_record.source='vcenter'）powerstate＝開機中，且 floor <= collected_at <= 參考掃描時間
     （不採用未來資料）才算在線證據；兩邊都有 IP 時 IP 對不上不得退回只比主機名。
     限制：source_record 只有「匯入時間」(collected_at)，沒有來源觀測時間，匯入舊檔會被當成新鮮；WhatsUp 沒有「受監控且 up」的資料表，沒做。
  6. 既有未處理「異常消失」每次依新規則重算；不符合的「預計自動關閉」。預設是預覽模式（disappear_autoclose_apply=0）：
     只把清單寫進 comparison_auto_close_log（applied=0），不改 comparison_result；管理員按「套用」才真的關閉
     （is_read=1，保留關閉前 note 全欄快照、規則版本與門檻快照，可由還原 API 還原）。
  7. 同一段連續未見期間只能有一筆事件（episode＝主機身分＋最後一次掃到的批次）：人工處理過的不重開，重新出現後再消失才開新的。
  8. 整次判定（品質＋新增／更新事件＋note＋自動關閉＋稽核）是單一交易，任何錯誤全部 rollback 並在活動紀錄留錯誤。

時間格式：scan_history.scan_time 由 db._now_local() 寫台北本地 '%Y-%m-%d %H:%M:%S'（舊測試／資料可能是 'T' 分隔），
本檔一律以「前 10 碼＝日期」比對、解析時把 'T' 換成空白；沒有混用 UTC 與本地的格式（若日後出現請先正規化再判定）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from db import get_setting, set_setting

RULE_VERSION = "2026-11-20.2"

DEFAULT_CONFIRM_SCANS = 5          # 連續幾次完整掃描都沒掃到才算消失
DEFAULT_DROP_PCT = 20              # 台數驟降門檻（%）
DEFAULT_SEGMENT_MIN_HOSTS = 5      # 網段原本至少幾台，才套用「台數驟降」
DEFAULT_CROSSCHECK_HOURS = 36      # vCOps 紀錄多新才當證據

K_CONFIRM = "missing_confirm_scans"
K_DROP = "scan_drop_threshold_pct"
K_SEGMIN = "scan_drop_segment_min_hosts"
K_XHOURS = "missing_crosscheck_hours"
K_APPLY = "disappear_autoclose_apply"

#: 設定的合法範圍（含頭尾）；超出範圍不靜默：用預設並留錯誤
_RANGES = {K_CONFIRM: (2, 60), K_DROP: (1, 99), K_SEGMIN: (1, 1000), K_XHOURS: (1, 720)}
#: 往回看的天數（日曆日）。不夠（舊紀錄的主機在視窗內找不到）時依序擴大
LOOKBACK_DAYS = (60, 180, 400)

_ON = {"poweredon", "on", "power on", "powered on"}


# ---------------------------------------------------------------- 設定 / 基本工具

def get_config(conn) -> dict:
    cfg: dict = {"errors": []}
    for key, default, name in ((K_CONFIRM, DEFAULT_CONFIRM_SCANS, "confirm"), (K_DROP, DEFAULT_DROP_PCT, "drop_pct"),
                               (K_SEGMIN, DEFAULT_SEGMENT_MIN_HOSTS, "seg_min"), (K_XHOURS, DEFAULT_CROSSCHECK_HOURS, "xhours")):
        raw = get_setting(conn, key, None)
        lo, hi = _RANGES[key]
        val = default
        if raw is not None and str(raw).strip() != "":
            try:
                v = int(str(raw).strip())
                if lo <= v <= hi:
                    val = v
                else:
                    cfg["errors"].append(f"設定 {key}={raw!r} 超出合理範圍 {lo}～{hi}，已改用預設值 {default}")
            except (TypeError, ValueError):
                cfg["errors"].append(f"設定 {key}={raw!r} 不是整數，已改用預設值 {default}")
        cfg[name] = val
    cfg["apply"] = str(get_setting(conn, K_APPLY, "0")).strip() == "1"
    return cfg


def _snapshot(cfg: dict) -> str:
    return json.dumps({k: cfg[k] for k in ("confirm", "drop_pct", "seg_min", "xhours", "apply")}, ensure_ascii=False)


def _norm(s) -> str:
    return (s or "").strip().lower()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _seg(s) -> str | None:
    """網段鍵：正規化 CIDR；空值＝None（品質未知，不累加消失）。"""
    t = str(s or "").strip()
    if not t:
        return None
    import scan_scope

    return scan_scope.norm_cidr(t) or t


def _ident(ip, hostname) -> str | None:
    ip = (ip or "").strip()
    if ip:
        return ip
    n = _norm(hostname)
    return "h:" + n if n else None


def _parse(ts) -> datetime | None:
    if not ts:
        return None
    t = str(ts).replace("T", " ")
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(t[:n], fmt)
        except ValueError:
            continue
    return None


def _day(ts) -> str:
    return str(ts).replace("T", " ")[:10]


def _ensure_schema(conn) -> None:
    """開發中途版本建過表的 DB 補欄位（CREATE IF NOT EXISTS 不會補）。2.069.2 首次出貨即為最終 schema，正式機不需遷移；
    這裡只讓開發機上早期版本建的表不用手動重建。必須在任何 DML 之前呼叫。"""
    want = {
        "comparison_note": {"last_seen": "TEXT"},
        "comparison_auto_close_log": {
            "prev_note": "TEXT", "prev_miss_count": "INTEGER", "prev_first_missing": "TEXT", "prev_updated_at": "TEXT",
            "prev_last_seen": "TEXT", "prev_note_existed": "INTEGER", "rule_version": "TEXT", "config_json": "TEXT",
            "applied": "INTEGER DEFAULT 1", "applied_at": "TEXT", "applied_by": "TEXT",
            "restored_by": "TEXT", "restore_reason": "TEXT"},
        "scan_batch_quality": {"new_baseline": "TEXT", "scope": "TEXT", "total_count": "INTEGER",
                               "rule_version": "TEXT", "config_json": "TEXT"},
    }
    for table, cols in want.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue
        for c, t in cols.items():
            if c not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {c} {t}")


# ---------------------------------------------------------------- 載入批次 → 日曆日

def _load_raw(conn, cur_time: str, lookback: int):
    cutoff = ((_parse(cur_time) or datetime.now()) - timedelta(days=lookback)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT scan_time, ip, hostname, segment, scan_ok FROM scan_history "
        "WHERE scan_time >= ? AND scan_time <= ? ORDER BY scan_time, id", (cutoff, cur_time)).fetchall()
    covs: list = []
    try:
        covs = conn.execute("SELECT scan_time, cidr, ok FROM scan_coverage WHERE scan_time >= ? AND scan_time <= ?",
                            (cutoff, cur_time)).fetchall()
    except sqlite3.OperationalError:
        covs = []        # 舊庫沒有 scan_coverage（只有 scan_history）：整批退回舊做法
    return cutoff, rows, covs


def _seed_ref(conn, cutoff: str) -> dict:
    """視窗之前的起始基準（避免視窗滑動改寫結論）。只能取「已驗證完整」的批次（scan_batch_quality.incomplete=0，
    採新基準事件那天也是 incomplete=0）；沒有就不當種子，從視窗內第一個完整批開始——寧可少一次證據，
    不拿不完整批當基準。回 {seg: set(身分)}。"""
    try:
        r = conn.execute("SELECT MAX(scan_time) t FROM scan_batch_quality WHERE incomplete = 0 AND scan_time < ?",
                         (cutoff,)).fetchone()
    except sqlite3.OperationalError:  # noqa: BLE001 - 沒有品質表＝沒有任何已驗證完整的批次，不當種子，從視窗內第一個完整批開始（不是錯誤）
        return {}
    if r is None or r["t"] is None:
        return {}
    keys: dict[str, set] = {}
    for x in conn.execute("SELECT ip, hostname, segment FROM scan_history WHERE scan_time = ? AND scan_ok = 1", (r["t"],)):
        seg, ident = _seg(x["segment"]), _ident(x["ip"], x["hostname"])
        if seg is not None and ident:
            keys.setdefault(seg, set()).add(ident)
    return keys


def build_days(rows, covs, ensure_time: str | None = None) -> tuple[list[dict], dict]:
    """把批次合併成日曆日。回 (days 舊→新, last_rows)。last_rows：{ident: (ip, hostname, seg)} 最後一次出現。"""
    batches: dict[str, dict] = {}

    def nb():
        return {"ips": set(), "names": set(), "segkeys": {}, "failrows": set(), "cov": {}}

    last_rows: dict[str, tuple] = {}
    for r in rows:
        b = batches.setdefault(r["scan_time"], nb())
        if r["scan_ok"] == 1:
            ident = _ident(r["ip"], r["hostname"])
            if not ident:
                continue
            if r["ip"]:
                b["ips"].add(r["ip"].strip())
            if r["hostname"]:
                b["names"].add(_norm(r["hostname"]))
            seg = _seg(r["segment"])
            if seg is not None:
                b["segkeys"].setdefault(seg, set()).add(ident)
            last_rows[ident] = (r["ip"], r["hostname"], seg)
        else:
            fs = _seg(r["segment"])
            if fs is not None:
                b["failrows"].add(fs)
    if ensure_time and ensure_time not in batches:
        batches[ensure_time] = nb()            # 這批沒有任何列（0 台／沒有 CIDR 的空來源）也要成為一個批次，才有品質紀錄
    for c in covs:
        b = batches.setdefault(c["scan_time"], nb())
        seg = _seg(c["cidr"])
        if seg is not None:
            b["cov"][seg] = bool(b["cov"].get(seg, True)) and bool(c["ok"])
    days: dict[str, dict] = {}
    for t in sorted(batches):
        b = batches[t]
        d = days.setdefault(_day(t), {"day": _day(t), "t": t, "ips": set(), "names": set(), "ok_segs": {},
                                      "failed": set(), "legacy": False, "scope": "full"})
        d["t"] = t
        d["ips"] |= b["ips"]
        d["names"] |= b["names"]
        if b["cov"]:
            # 有 coverage 時它是範圍的唯一依據：只有 ok=1 的 CIDR 參與「未見」累加；
            # 沒登記在 coverage 的成功列只能證明「有看到」（presence），不能證明「沒看到」。
            ok = {s for s, o in b["cov"].items() if o}
            failed = {s for s, o in b["cov"].items() if not o}
        else:
            d["legacy"] = True
            failed = set(b["failrows"])
            ok = {s for s in b["segkeys"] if s not in failed}
        for s in ok:
            d["ok_segs"][s] = set(b["segkeys"].get(s, ()))          # 身分集合（不是列數、也不是各網段計數相加）
        d["failed"] |= failed
    for d in days.values():
        d["failed"] -= set(d["ok_segs"])          # 同一天有一批成功涵蓋就不算失敗
        d["ids"] = set().union(*d["ok_segs"].values()) if d["ok_segs"] else set()
        d["total"] = len(d["ids"])                 # 整批台數＝各網段身分集合聯集的 distinct 數（同一身分在兩個網段只算一台）
    return [days[k] for k in sorted(days)], last_rows


# ---------------------------------------------------------------- 完整度判定

def judge_days(days: list[dict], cfg: dict, seed: dict) -> None:
    """依序判定每個日曆日。參考基準（各網段的身分集合）只在完整批次或「連續 N 批縮減確認＝採新基準」時才更新。"""
    drop = cfg["drop_pct"] / 100.0
    confirm, smin = cfg["confirm"], cfg["seg_min"]
    ref: dict[str, set] = {k: set(v) for k, v in seed.items()}
    sseg: dict[str, int] = {}
    stot = 0
    for d in days:
        reasons: list[str] = []
        events: list[str] = []
        bad: set[str] = set()
        pend: dict[str, set] = {}
        explained: set = set()                 # 被「網段縮減」解釋掉的身分（不管最後算不算不完整，都由網段那層處理）
        consider = set(d["ok_segs"]) | (set(ref) if d["legacy"] else set())
        for seg in sorted(consider):
            cur, r = d["ok_segs"].get(seg, set()), ref.get(seg, set())
            cnt = len(cur)
            if len(r) >= smin and cnt < len(r) * (1 - drop):
                explained |= r
                n = sseg.get(seg, 0) + 1
                if n < confirm:
                    sseg[seg] = n
                    bad.add(seg)
                    reasons.append(f"網段 {seg} 台數 {cnt} 比參考 {len(r)} 少超過 {cfg['drop_pct']}%")
                else:
                    sseg[seg] = 0
                    pend[seg] = set(cur)
                    events.append(f"已採新基準：網段 {seg} 由 {len(r)} 台降為 {cnt} 台（連續 {confirm} 批縮減）")
            else:
                sseg[seg] = 0
                pend[seg] = set(cur)
        for seg in sorted(d["failed"]):
            reasons.append(f"網段 {seg} 掃描失敗")
        glob = False
        if not d["ok_segs"]:
            glob = True
            reasons.append("沒有任何網段掃描成功")
        elif d["total"] == 0:
            glob = True
            reasons.append("掃描成功但沒有掃到任何主機（0 台）")
        else:
            prior = set().union(*(ref.get(s, set()) for s in consider)) if consider else set()
            residual = len(d["ids"] | explained)
            if len(prior) >= smin and residual < len(prior) * (1 - drop):
                n = stot + 1
                if n < confirm:
                    stot = n
                    glob = True
                    reasons.append(f"總台數 {d['total']} 比參考 {len(prior)} 少超過 {cfg['drop_pct']}%")
                else:
                    stot = 0
                    events.append(f"已採新基準：整批總台數由 {len(prior)} 降為 {d['total']}（連續 {confirm} 批縮減）")
            else:
                stot = 0
        if not glob:
            ref.update(pend)
        d["global_incomplete"] = glob
        d["bad_segs"] = bad
        d["skip_segs"] = bad | d["failed"]
        d["valid_segs"] = set() if glob else (consider - bad)
        d["incomplete"] = glob or bool(d["skip_segs"])
        d["reason"] = "；".join(reasons)
        d["events"] = events
        d["scope"] = "all" if glob else ("segments" if d["skip_segs"] else "ok")


def load_days(conn, cur_time: str, lookback: int, cfg: dict):
    cutoff, rows, covs = _load_raw(conn, cur_time, lookback)
    days, last_rows = build_days(rows, covs, ensure_time=cur_time)
    judge_days(days, cfg, _seed_ref(conn, cutoff))
    return days, last_rows


# ---------------------------------------------------------------- 單台判定

def _present(d: dict, ip, name: str) -> bool:
    return bool((ip and ip.strip() in d["ips"]) or (name and name in d["names"]))


def evaluate_host(days: list[dict], ip, hostname, seg) -> dict:
    """由新到舊走訪日曆日：掃到＝停（歸零）；沒掃到＋該網段當天有效＝+1；當天不完整＝跳過不計；不在範圍＝不理。"""
    name = _norm(hostname)
    miss, first, last_seen, skipped, seen = 0, None, None, 0, False
    for d in reversed(days):
        if _present(d, ip, name):
            last_seen, seen = d["t"], True
            break
        if seg is None:
            continue                                   # 品質未知：不累加
        if d["global_incomplete"] or seg in d["skip_segs"]:
            skipped += 1
        elif seg in d["valid_segs"]:
            miss += 1
            first = d["t"]
    return {"miss": miss, "first_missing": first, "last_seen": last_seen, "seen": seen, "skipped": skipped,
            "present_now": bool(days) and _present(days[-1], ip, name)}


def _note(info: dict) -> str:
    if info["miss"] <= 0:
        return ""
    s = f"最近 {info['miss']} 次完整掃描未見（首次未見：{_day(info['first_missing'])}）"
    if info.get("skipped"):
        s += f"；另有 {info['skipped']} 次掃描不完整未計入"
    return s


# ---------------------------------------------------------------- 交叉確認（vCOps）

def load_alive_index(conn, ref_time: str, hours: int) -> dict:
    """floor <= collected_at <= 參考掃描時間、powerstate＝開機中的 VM。
    {'ip': {ip: 說明}, 'name': {名稱: [(vm_ip|None, 說明)]}}。不採用比參考時間晚的資料。"""
    from machine_identity import is_generic, norm_name

    out: dict = {"ip": {}, "name": {}}
    ref = _parse(ref_time) or datetime.now()
    try:
        recs = conn.execute("SELECT payload, collected_at FROM source_record WHERE source = 'vcenter'").fetchall()
    except sqlite3.Error:
        return out
    floor = ref - timedelta(hours=hours)
    for r in recs:
        at = _parse(r["collected_at"])
        if at is None or at < floor or at > ref:
            continue
        try:
            pl = json.loads(r["payload"] or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(pl, dict) or str(pl.get("powerstate") or "").strip().lower() not in _ON:
            continue
        why = f"vCOps 近期紀錄顯示開機中（{str(r['collected_at'])[:16]}）"
        ip = str(pl.get("ip") or "").strip()
        if ip:
            out["ip"][ip] = why
        for k in ("hostname", "vm_name", "dns_name"):
            nm = norm_name(pl.get(k))
            if nm and not is_generic(nm):
                out["name"].setdefault(nm, []).append((ip or None, why))
    return out


def alive_reason(index: dict, ip, hostname) -> str | None:
    from machine_identity import is_generic, norm_name

    ip = (ip or "").strip() or None
    if ip and ip in index["ip"]:
        return index["ip"][ip]
    nm = norm_name(hostname)
    if nm and not is_generic(nm):
        for vip, why in index["name"].get(nm, ()):
            if ip and vip and vip != ip:
                continue                  # 兩邊都有 IP 卻對不上＝不同台，不退回只比主機名
            return why
    return None


# ---------------------------------------------------------------- 寫入（全部不自行 commit）

def _insert_result(conn, hostname, ip) -> int:
    cur = conn.execute("INSERT INTO comparison_result (hostname, ip, issue_type, detected_at) VALUES (?, ?, '異常消失', ?)",
                       (hostname, ip, _now()))
    return cur.lastrowid


def _set_note(conn, rid: int, info: dict, note: str) -> None:
    conn.execute(
        "INSERT INTO comparison_note (result_id, note, miss_count, first_missing, last_seen, updated_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(result_id) DO UPDATE SET note=excluded.note, miss_count=excluded.miss_count, "
        "first_missing=excluded.first_missing, last_seen=excluded.last_seen, updated_at=excluded.updated_at",
        (rid, note, info.get("miss"), info.get("first_missing"), info.get("last_seen"), _now()))


def _auto_close(conn, rid: int, reason: str, cfg: dict, by: str | None = None, log_id: int | None = None) -> None:
    """真正關閉（is_read=1）。先把關閉前 note 全欄存進 log；關閉後 note 結構欄清空、說明改成關閉原因。"""
    row = conn.execute("SELECT is_read, handled_at FROM comparison_result WHERE id = ?", (rid,)).fetchone()
    n = conn.execute("SELECT * FROM comparison_note WHERE result_id = ?", (rid,)).fetchone()
    now = _now()
    vals = (reason, row["is_read"], row["handled_at"], n["note"] if n else None, n["miss_count"] if n else None,
            n["first_missing"] if n else None, n["updated_at"] if n else None, n["last_seen"] if n else None,
            1 if n else 0, RULE_VERSION, _snapshot(cfg), now, by)
    if log_id is None:
        conn.execute(
            "INSERT INTO comparison_auto_close_log (result_id, closed_at, reason, prev_is_read, prev_handled_at, prev_note, "
            "prev_miss_count, prev_first_missing, prev_updated_at, prev_last_seen, prev_note_existed, rule_version, "
            "config_json, applied, applied_at, applied_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
            (rid, now, reason, row["is_read"], row["handled_at"], vals[3], vals[4], vals[5], vals[6], vals[7], vals[8],
             RULE_VERSION, _snapshot(cfg), now, by))
    else:
        conn.execute(
            "UPDATE comparison_auto_close_log SET closed_at=?, reason=?, prev_is_read=?, prev_handled_at=?, prev_note=?, "
            "prev_miss_count=?, prev_first_missing=?, prev_updated_at=?, prev_last_seen=?, prev_note_existed=?, "
            "rule_version=?, config_json=?, applied=1, applied_at=?, applied_by=? WHERE id=?",
            (now, reason, row["is_read"], row["handled_at"], vals[3], vals[4], vals[5], vals[6], vals[7], vals[8],
             RULE_VERSION, _snapshot(cfg), now, by, log_id))
    conn.execute("UPDATE comparison_result SET is_read = 1, handled_at = ? WHERE id = ?", (now, rid))
    conn.execute(
        "INSERT INTO comparison_note (result_id, note, miss_count, first_missing, last_seen, updated_at) VALUES (?,?,NULL,NULL,?,?) "
        "ON CONFLICT(result_id) DO UPDATE SET note=excluded.note, miss_count=NULL, first_missing=NULL, updated_at=excluded.updated_at",
        (rid, f"系統自動關閉：{reason}", n["last_seen"] if n else None, now))


def restore_auto_closed(conn, result_id: int, by: str | None = None, reason: str | None = None) -> str:
    """還原一筆被系統自動關閉的紀錄。回 'ok'／'not_found'／'not_auto_closed'／'already_restored'。逐欄恢復，不刪原說明。"""
    if conn.execute("SELECT 1 FROM comparison_result WHERE id = ?", (result_id,)).fetchone() is None:
        return "not_found"
    log = conn.execute("SELECT * FROM comparison_auto_close_log WHERE result_id = ? AND applied = 1 ORDER BY id DESC LIMIT 1",
                       (result_id,)).fetchone()
    if log is None:
        return "not_auto_closed"
    if log["restored_at"] is not None:
        return "already_restored"
    try:
        conn.execute("UPDATE comparison_result SET is_read = ?, handled_at = ? WHERE id = ?",
                     (log["prev_is_read"], log["prev_handled_at"], result_id))
        if log["prev_note_existed"]:
            conn.execute("UPDATE comparison_note SET note=?, miss_count=?, first_missing=?, last_seen=?, updated_at=? WHERE result_id=?",
                         (log["prev_note"], log["prev_miss_count"], log["prev_first_missing"], log["prev_last_seen"],
                          log["prev_updated_at"], result_id))
        else:
            conn.execute("DELETE FROM comparison_note WHERE result_id = ?", (result_id,))   # 這筆 note 是自動關閉當下才建的
        conn.execute("UPDATE comparison_auto_close_log SET restored_at=?, restored_by=?, restore_reason=? WHERE id=?",
                     (_now(), by, reason, log["id"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return "ok"


class StalePreview(Exception):
    """套用時預覽清單跟確認當下不一致。"""


def _preview_rows(conn) -> list:
    return conn.execute(
        "SELECT l.id AS log_id, l.result_id AS id, r.hostname, r.ip, l.reason FROM comparison_auto_close_log l "
        "JOIN comparison_result r ON r.id = l.result_id WHERE l.applied = 0 AND l.restored_at IS NULL "
        "AND r.is_read = 0 ORDER BY l.result_id").fetchall()


def _preview_hash(conn, rows) -> str:
    """預覽版本雜湊：涵蓋 result_id 清單、reason、規則版本、設定值。清單被換掉（即使筆數相同）雜湊就不同。"""
    import hashlib

    cfg = get_config(conn)
    payload = json.dumps({"items": [[r["id"], r["reason"]] for r in rows], "rule": RULE_VERSION, "cfg": _snapshot(cfg)},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def preview_list(conn) -> dict:
    rows = _preview_rows(conn)
    return {"count": len(rows), "apply_mode": get_config(conn)["apply"], "preview_hash": _preview_hash(conn, rows),
            "items": [dict(r) for r in rows]}


def apply_preview(conn, by: str | None = None, preview_hash: str | None = None) -> int:
    """把預覽清單（applied=0）真正套用，並把設定改為已套用。回實際關閉筆數。
    確認綁清單內容：在同一個 BEGIN IMMEDIATE 交易內重算預覽雜湊，跟管理員確認當下的不一致就 StalePreview（API 回 409）；
    套用只關閉預覽時列出的那些 id。"""
    cfg = get_config(conn)
    n = 0
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = _preview_rows(conn)
        if preview_hash is None or _preview_hash(conn, rows) != preview_hash:
            raise StalePreview("預覽清單已變動，請重新預覽再確認")
        for lg in rows:
            _auto_close(conn, lg["id"], lg["reason"], cfg, by, log_id=lg["log_id"])
            n += 1
        set_setting(conn, K_APPLY, "1", commit=False)
        import activity

        activity.log(conn, username=by, ip=None, method="POST", path="/api/issues/auto-close/apply",
                     status=200, duration_ms=0, action=f"disappear_autoclose_apply:{n}", commit=False)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return n


# ---------------------------------------------------------------- 品質紀錄

def save_batch_quality(conn, days: list[dict], cfg: dict) -> None:
    for d in days:
        conn.execute(
            "INSERT INTO scan_batch_quality (scan_time, ok_count, incomplete, global_incomplete, bad_segments, reason, evaluated_at, "
            "new_baseline, scope, total_count, rule_version, config_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(scan_time) DO UPDATE SET ok_count=excluded.ok_count, incomplete=excluded.incomplete, "
            "global_incomplete=excluded.global_incomplete, bad_segments=excluded.bad_segments, reason=excluded.reason, "
            "evaluated_at=excluded.evaluated_at, new_baseline=excluded.new_baseline, scope=excluded.scope, "
            "total_count=excluded.total_count, rule_version=excluded.rule_version, config_json=excluded.config_json",
            (d["t"], d["total"], int(d["incomplete"]), int(d["global_incomplete"]),
             json.dumps(sorted(d["skip_segs"]), ensure_ascii=False), d["reason"], _now(),
             json.dumps(d["events"], ensure_ascii=False), d["scope"], d["total"], RULE_VERSION, _snapshot(cfg)))


def latest_quality(conn) -> dict | None:
    """畫面用：最近一次掃描批次的品質＋有效門檻。沒有任何紀錄回 None（讀取失敗由 API 層回錯誤，不在這裡吞）。"""
    r = conn.execute("SELECT * FROM scan_batch_quality ORDER BY scan_time DESC LIMIT 1").fetchone()
    if r is None:
        return None
    cfg = get_config(conn)
    # 最新一批掃描（含 0 台／全失敗）比品質紀錄新，或該批判定失敗過＝「最新一批判定未完成」，不可拿舊批的完整當現在的
    latest_scan = conn.execute("SELECT MAX(scan_time) t FROM scan_history").fetchone()["t"]
    try:
        cov_t = conn.execute("SELECT MAX(scan_time) t FROM scan_coverage").fetchone()["t"]
    except sqlite3.OperationalError:
        cov_t = None
    latest_scan = max([t for t in (latest_scan, cov_t) if t], default=None)
    pending = r["scope"] == "failed" or bool(latest_scan and str(latest_scan) > str(r["scan_time"]))
    events: list[str] = []
    for x in conn.execute("SELECT scan_time, new_baseline FROM scan_batch_quality ORDER BY scan_time DESC LIMIT 7"):
        for e in json.loads(x["new_baseline"] or "[]"):
            events.append(f"{_day(x['scan_time'])} {e}")
    bad = json.loads(r["bad_segments"] or "[]")
    glob = bool(r["global_incomplete"])
    return {"scan_time": r["scan_time"], "ok_count": r["ok_count"], "incomplete": bool(r["incomplete"]),
            "global_incomplete": glob, "mode": "pending" if pending else ("all" if glob else ("segments" if bad else "ok")),
            "judgement_pending": pending, "latest_scan_time": latest_scan,
            "bad_segments": bad, "reason": r["reason"] or "", "baseline_events": events,
            "confirm": cfg["confirm"], "drop_pct": cfg["drop_pct"], "rule_version": RULE_VERSION,
            "autoclose_apply": cfg["apply"], "invalid_settings": cfg["errors"]}


# ---------------------------------------------------------------- 主流程

CANDIDATE_DAYS = 400


def _load_older(conn, cur_time: str, lookback: int, known_idents: set, known_ips: set, known_names: set) -> list:
    """判定視窗之前、CANDIDATE_DAYS 天內最後出現的主機：[(ip, hostname, seg, 最後出現時間)]。"""
    now = _parse(cur_time) or datetime.now()
    hi = (now - timedelta(days=lookback)).strftime("%Y-%m-%d")
    lo = (now - timedelta(days=CANDIDATE_DAYS)).strftime("%Y-%m-%d")
    out: dict[str, tuple] = {}
    for r in conn.execute("SELECT ip, hostname, segment, MAX(scan_time) t FROM scan_history WHERE scan_ok = 1 "
                          "AND scan_time >= ? AND scan_time < ? GROUP BY ip, hostname, segment", (lo, hi)):
        ident = _ident(r["ip"], r["hostname"])
        if not ident or ident in known_idents:
            continue
        if (r["ip"] and r["ip"].strip() in known_ips) or (r["hostname"] and _norm(r["hostname"]) in known_names):
            continue                           # 視窗內還看得到同一台（換了識別欄位）
        if ident not in out or r["t"] > out[ident][3]:
            out[ident] = (r["ip"], r["hostname"], _seg(r["segment"]), r["t"])
    return list(out.values())


def _plan(conn, cur_time: str, cfg: dict, lookback: int) -> dict | None:
    days, last_rows = load_days(conn, cur_time, lookback, cfg)
    if not days:
        return None
    cur = days[-1]
    by_ip = {v[0].strip(): v for v in last_rows.values() if v[0]}
    by_name = {_norm(v[1]): v for v in last_rows.values() if v[1]}
    open_by_ip: dict[str, sqlite3.Row] = {}
    open_by_name: dict[str, sqlite3.Row] = {}
    open_recs = conn.execute("SELECT id, ip, hostname FROM comparison_result WHERE issue_type = '異常消失' AND is_read = 0").fetchall()
    for rec in open_recs:                      # 一次載入，不逐台 SELECT
        if rec["ip"]:
            open_by_ip.setdefault(rec["ip"].strip(), rec)
        if rec["hostname"]:
            open_by_name.setdefault(_norm(rec["hostname"]), rec)
    episodes: set[tuple[str, str]] = set()     # 人工處理過的事件：(身分, 最後一次掃到)
    for r in conn.execute(
            "SELECT r.ip, r.hostname, n.last_seen FROM comparison_result r JOIN comparison_note n ON n.result_id = r.id "
            "WHERE r.issue_type = '異常消失' AND r.is_read = 1 AND n.last_seen IS NOT NULL AND r.id NOT IN "
            "(SELECT result_id FROM comparison_auto_close_log WHERE applied = 1 AND restored_at IS NULL)"):
        for k in (_ident(r["ip"], None), _ident(None, r["hostname"])):
            if k:
                episodes.add((k, _day(r["last_seen"])))
    alive = load_alive_index(conn, cur_time, cfg["xhours"])
    # 管理員還原過的紀錄＝人工覆核過，不再自動關閉（否則下次掃描又被關掉，還原就沒意義）
    restored = {r["result_id"] for r in conn.execute(
        "SELECT result_id FROM comparison_auto_close_log WHERE restored_at IS NOT NULL")}

    creates, updates, closes = [], [], []
    seen_keys: set[str] = set()
    need_more = False
    # 候選母體固定涵蓋 CANDIDATE_DAYS 天：判定視窗之前最後出現的主機（例如 80 天前）也要能成為新事件候選，
    # 不能只因為「既有未處理事件找不到主機」才擴大視窗。視窗內所有完整批次它都沒掃到＝全部算未見。
    older = _load_older(conn, cur_time, lookback, set(last_rows), {v[0].strip() for v in last_rows.values() if v[0]},
                        {_norm(v[1]) for v in last_rows.values() if v[1]})
    cands = [(ip, hn, seg, None) for (ip, hn, seg) in last_rows.values()] + older
    for ip, hn, seg, old_t in cands:
        if _present(cur, ip, _norm(hn)) or seg is None:
            continue
        info = evaluate_host(days, ip, hn, seg)
        if old_t is not None:
            info["seen"], info["last_seen"] = True, old_t
        if not info["seen"] or info["miss"] < cfg["confirm"]:
            continue
        rec = (open_by_ip.get((ip or "").strip()) if ip else None) or open_by_name.get(_norm(hn))
        if rec is not None:
            continue                           # 既有未處理紀錄由下面「重算」一次處理（更新說明或自動關閉）
        if alive_reason(alive, ip, hn):
            continue
        ld = _day(info["last_seen"])
        if any((k, ld) in episodes for k in (_ident(ip, None), _ident(None, hn)) if k):
            continue                           # 同一段未見期間已被人工處理過：不重開
        keys = {k for k in (_ident(ip, None), _ident(None, hn)) if k}
        if keys & seen_keys:
            continue
        seen_keys |= keys
        creates.append((ip, hn, info))
    for rec in open_recs:
        row = (by_ip.get(rec["ip"].strip()) if rec["ip"] else None) or by_name.get(_norm(rec["hostname"]))
        if row is None:
            need_more = True                   # 視窗內找不到這台：之後可能要往更早找
            continue
        info = evaluate_host(days, rec["ip"], rec["hostname"], row[2])
        if row[2] is None:
            continue
        why = None
        if info["present_now"] or info["miss"] == 0:
            why = f"最近一次掃描已重新掃到（{_day(info['last_seen'])}）" if info["last_seen"] else "已重新掃到"
            if not info["present_now"] and info["miss"] == 0 and info["skipped"]:
                why = f"最近的掃描都不完整未計入，未見次數為 0（{info['skipped']} 次略過）"
        elif info["miss"] < cfg["confirm"]:
            why = f"最近完整掃描連續未見僅 {info['miss']} 次，未達 {cfg['confirm']} 次門檻"
        else:
            a = alive_reason(alive, rec["ip"], rec["hostname"])
            if a:
                why = f"交叉確認仍在線：{a}"
        if why and rec["id"] not in restored:
            closes.append((rec["id"], why))
        else:
            updates.append((rec["id"], info))
    return {"days": days, "creates": creates, "updates": updates, "closes": closes, "need_more": need_more}


def detect_disappearances(conn, current_scan_time: str) -> dict:
    """依新規則產生「異常消失」、重算既有未處理紀錄。單一交易；失敗全部 rollback 並在活動紀錄留錯誤。
    回 {"新增", "自動關閉"（預覽模式＝預計關閉數，不動資料）, "預覽": bool, "錯誤": str|None}。"""
    out = {"新增": 0, "自動關閉": 0, "預覽": True, "錯誤": None}
    cfg = get_config(conn)
    out["預覽"] = not cfg["apply"]
    try:
        conn.commit()                          # 不把呼叫端未提交的東西捲進來；之後到 commit 前都不得自行提交
        _ensure_schema(conn)
        try:
            import scan_scope

            scan_scope._ensure(conn)
        except Exception:  # noqa: BLE001 - 沒有 scan_coverage 就退回舊批次做法，不影響判定
            pass
        plan = None
        for lb in LOOKBACK_DAYS:
            plan = _plan(conn, current_scan_time, cfg, lb)
            if plan is None or not plan["need_more"]:
                break
        if plan is None:
            conn.commit()
            return out
        save_batch_quality(conn, plan["days"], cfg)
        for ip, hn, info in plan["creates"]:
            rid = _insert_result(conn, hn, ip)
            _set_note(conn, rid, info, _note(info))
            out["新增"] += 1
        for rid, info in plan["updates"]:
            _set_note(conn, rid, info, _note(info))
        conn.execute("DELETE FROM comparison_auto_close_log WHERE applied = 0 AND restored_at IS NULL")
        for rid, why in plan["closes"]:
            if cfg["apply"]:
                _auto_close(conn, rid, why, cfg)
            else:
                conn.execute("INSERT INTO comparison_auto_close_log (result_id, closed_at, reason, applied, rule_version, config_json) "
                             "VALUES (?,?,?,0,?,?)", (rid, _now(), why, RULE_VERSION, _snapshot(cfg)))
            out["自動關閉"] += 1
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - 整個交易回復並留下可見紀錄；掃描本身已成功，不讓它被標成失敗
        conn.rollback()
        out["錯誤"] = f"{type(exc).__name__}: {exc}"[:300]
        _record_failure(conn, current_scan_time, out["錯誤"])      # 獨立小交易：不會被剛才的 rollback 帶走
        _log_error(conn, out["錯誤"])
        return out
    for msg in cfg["errors"]:
        _log_error(conn, msg)
    return out


def _record_failure(conn, scan_time: str, msg: str) -> None:
    """本批判定失敗要持久化（scan-quality 看得到「最新一批判定未完成」），不能只靠回傳值被呼叫端忽略。"""
    try:
        conn.execute(
            "INSERT INTO scan_batch_quality (scan_time, ok_count, incomplete, global_incomplete, bad_segments, reason, "
            "evaluated_at, new_baseline, scope, total_count, rule_version) VALUES (?,0,1,1,'[]',?,?,'[]','failed',0,?) "
            "ON CONFLICT(scan_time) DO UPDATE SET incomplete=1, global_incomplete=1, reason=excluded.reason, "
            "evaluated_at=excluded.evaluated_at, scope='failed'",
            (scan_time, ("本批判定失敗：" + msg)[:300], _now(), RULE_VERSION))
        conn.commit()
    except Exception:  # noqa: BLE001 - 連失敗紀錄都寫不進去時，仍有活動紀錄（_log_error）與回傳值
        conn.rollback()


def _log_error(conn, msg: str) -> None:
    """錯誤／設定問題寫進活動紀錄（使用者看得到），本身失敗也不外拋。"""
    try:
        import activity

        activity.log(conn, username=None, ip=None, method="SYSTEM", path="/internal/disappear_rule", status=500,
                     duration_ms=0, action=("disappear_rule_error: " + msg)[:200])
    except Exception:  # noqa: BLE001 - 連紀錄都寫不進去時沒有更下游可以回報
        pass
