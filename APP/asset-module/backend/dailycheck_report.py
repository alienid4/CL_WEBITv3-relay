"""1.5 每日檢查日報（Teams／信件，kind=summary_dailycheck）：每天 dailycheck_report_time 排一張短卡。

口徑全部沿用 1.5 畫面，不自創：
  - 數字：今天（台北日期）最新一次 daily_check_run 的逐列；異常＝verdict bad、無資料＝nodata（daily_check.counts_of）。
  - 資料來源狀態：frontend/utils/dailycheckSources.ts 的逐行 Python 移植（六種狀態）。畫面那份是純前端函式，後端沒有現成的，
    所以移植一份；tests/asset_module/test_teams_dailycheck_report.py 用 node 直接載入 .ts 跟這份逐案對照，兩邊漂移就紅。
  - 夜間通知：night_desk.notified（同「今晚通知」那一份規則），讀不到就寫「讀不到」，不寫成 0。
只讀：不寫任何表（預覽路徑在 read_only 連線底下呼叫）。
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime

MAX_ABNORMAL_LINES = 5
CONFIRM_WARN = "⚠ 尚未確認送出（以下是系統判定，僅供參考）"

# ── 資料來源狀態（dailycheckSources.ts 移植）──────────────────────────
SRC_DEFS = [("alert", "WhatsUp 告警"), ("lastpoll", "WhatsUp 上次輪循"), ("perf", "WhatsUp 前一日效能與磁碟"),
            ("vmware", "VMware（vROps）"), ("storage", "Unity 儲存"), ("adtime", "AD 對時")]
_RANK = {"失敗": 5, "沒收到": 4, "過期": 3, "只收到一半": 2, "待設定": 1, "正常": 0}
_METRIC_NAME = {"cpu": "CPU", "mem": "記憶體", "disk": "硬碟"}
VM_DAILY_MAX_MIN = 26 * 60
VM_DUE_HM = "07:45"


def _cut(s: str, n: int = 90) -> str:
    return s[:n] + "…" if len(s) > n else s


def src_key_of(source) -> str:
    s = str(source or "")
    if re.search("告警快照", s):
        return "alert"
    if re.search("上次輪循", s):
        return "lastpoll"
    if re.search("vROps|VMware", s):
        return "vmware"
    if re.search("Unity", s):
        return "storage"
    if re.search("AD 對時", s):
        return "adtime"
    if re.search("效能|磁碟|健康檢查", s):
        return "perf"
    return ""


def partial_why(err) -> str:
    e = str(err or "")
    if not e:
        return ""
    if re.search("report cap", e, re.I):
        return "WhatsUp 一次最多給 100 台，逐台補問沒補完"
    if re.search("per-device cpu", e, re.I):
        return "逐台補問時 WhatsUp 沒回應"
    if re.search("time box", e, re.I):
        return "超過收集時限"
    if re.search("call budget|MaxCalls", e, re.I):
        return "呼叫次數用完"
    if re.search("page cap|pages read|more pages", e, re.I):
        return "報表頁數上限不夠，沒讀完"
    if re.search(r"HTTP\s*5\d\d|timed? ?out|connection", e, re.I):
        return "WhatsUp 沒回應"
    if re.search(r"HTTP\s*4\d\d", e, re.I):
        return "WhatsUp 拒絕查詢"
    return "收集沒讀完"


def _worst(a: str, b: str) -> str:
    return b if _RANK[b] > _RANK[a] else a


def reason_status(reason, has_time: bool) -> str:
    r = str(reason or "")
    if re.search("太舊|過期", r):
        return "過期"
    if re.search("失敗|錯誤|拒收", r):
        return "失敗"
    if re.search("不完整|沒讀完|不確定|未知", r):
        return "只收到一半"
    if re.search("未設定|未手填", r):
        return "待設定"
    return "失敗" if has_time else "沒收到"


def rows_status(rows: list[dict]) -> dict | None:
    rs = [r for r in (rows or []) if r and r.get("verdict") != "manual"]
    if not rs:
        return None
    ats = sorted(str(r.get("data_at") or "") for r in rs if r.get("data_at"))
    at = ats[-1] if ats else ""
    nod = [r for r in rs if r.get("verdict") == "nodata"]
    if not nod:
        return {"status": "正常", "at": at, "next": "不用處理"}
    st = "正常"
    for r in nod:
        st = _worst(st, reason_status(r.get("reason"), bool(r.get("data_at"))))
    codes = [str(r.get("code") or "") for r in nod if r.get("code")]
    why = str(nod[0].get("reason") or "—")
    if len(rs) - len(nod) > 0 and st != "待設定":
        return {"status": "只收到一半", "at": at, "next": _cut(f"缺 {'、'.join(codes)} 的資料：{why}")}
    return {"status": st, "at": at, "next": _cut(f"{'、'.join(codes)}：{why}")}


def _hm(t) -> str:
    return str(t or "")[11:16]


def _first_problem(b: dict) -> str:
    p = b.get("problems") or []
    return (p[0] if p else None) or "—"


def _age(lc) -> float:
    """age_min 缺值或不是數字：當「很舊」處理（獨立審查 P2：缺值會讓整張日報組不出來）。"""
    try:
        return float(lc.get("age_min"))
    except (TypeError, ValueError):
        return float("inf")


def lastpoll_status(lpst, fresh_min, now_hm: str, lp_alert=None) -> dict | None:
    if not lpst:
        return None
    latest = lpst.get("latest")
    if not latest:
        return {"status": "沒收到", "at": "", "next": "還沒收到資料，請確認中繼機收集排程"}
    at = str(latest.get("collected_at") or "")
    lc = lpst.get("latest_complete")
    groups = lpst.get("groups") or []
    failed = [g for g in groups if not g.get("ok")]
    if failed:
        return {"status": "失敗", "at": at, "next": _cut(f"群組「{failed[0].get('path')}」收集失敗：{failed[0].get('error') or '—'}"
                                                       + (f"（共 {len(failed)} 個群組）" if len(failed) > 1 else ""))}
    if lp_alert:
        return {"status": "過期", "at": at, "next": _cut(str(lp_alert))}
    if not lc:
        return {"status": "只收到一半", "at": at, "next": _cut(f"沒有完整的一批：{_first_problem(latest)}")}
    in_window = "05:30" <= now_hm <= "18:00"
    fresh = lpst.get("fresh_min")
    fresh = float(fresh if fresh is not None else (fresh_min if fresh_min is not None else 35))
    if in_window and _age(lc) > fresh:
        return {"status": "過期", "at": at, "next": f"{lc.get('age_min')} 分鐘沒更新，請確認收集排程"}
    if not latest.get("complete"):
        return {"status": "只收到一半", "at": at, "next": _cut(f"最新一批不完整（{_first_problem(latest)}）；判定用 {_hm(lc.get('collected_at'))} 那批完整的")}
    part = [g for g in groups if g.get("partial")]
    if part:
        g = part[0]
        k = g["partial"][0]
        pw = (g.get("partial_why") or {}).get(k) or {}
        why = partial_why(pw.get("error"))
        got = pw.get("devices")
        miss = f"讀到 {got}／{g.get('members')} 台" if got is not None and g.get("members") else ""
        tail = str(g.get("path") or "").split(">")[-1].strip()
        return {"status": "只收到一半", "at": at,
                "next": _cut(f"「{tail}」{_METRIC_NAME.get(k, k)} {'，'.join(x for x in (miss, why) if x)}；下一輪會自動再收")}
    unscoped = [g for g in groups if g.get("ok") and not g.get("scope_ok")]
    if unscoped:
        return {"status": "待設定", "at": at, "next": f"{len(unscoped)} 個群組成員變了（範圍未確認），要核准"}
    return {"status": "正常", "at": at, "next": "不用處理"}


def vmware_status(vmst, vm_configured: bool, now_ymd: str = "", now_hm: str = "") -> dict | None:
    if not vmst:
        return None
    latest = vmst.get("latest")
    if not latest:
        return {"status": "沒收到", "at": "", "next": "還沒收到任何一批（跳板機每天 06:00 起讀 vROps）"}
    at = str(latest.get("collected_at") or "")
    lc = vmst.get("latest_complete")
    if not lc:
        return {"status": "只收到一半", "at": at, "next": _cut(f"沒有完整的一批：{_first_problem(latest)}，VC 列會是無資料")}
    if _age(lc) > VM_DAILY_MAX_MIN:
        return {"status": "過期", "at": at, "next": f"最新完整一批是 {round(_age(lc) / 60)} 小時前，請檢查跳板機的排程"}
    if now_ymd and now_hm >= VM_DUE_HM and str(lc.get("collected_at") or "")[:10] < now_ymd:
        return {"status": "過期", "at": at, "next": f"今天 {VM_DUE_HM} 前沒收到完整的一批（最新是 {str(lc.get('collected_at') or '')[:16]}），請檢查跳板機的排程"}
    if not latest.get("complete"):
        return {"status": "只收到一半", "at": at, "next": _cut(f"最新一批不完整（{_first_problem(latest)}）；判定用 {_hm(lc.get('collected_at'))} 那批完整的")}
    if not vm_configured:
        return {"status": "待設定", "at": at, "next": "資料已收到，還沒設定 vCenter 清單（VC 列暫時人工）"}
    return {"status": "正常", "at": at, "next": "不用處理"}


def build_sources(rows: list[dict], lpst, vmst, perf_source, vm_configured: bool, lp_fresh_min, now_hm: str, now_ymd: str, lp_alert) -> list[dict]:
    by_key: dict[str, list[dict]] = {}
    for r in rows or []:
        k = src_key_of((r or {}).get("source"))
        if k:
            by_key.setdefault(k, []).append(r)
    out = []
    for idx, (key, name) in enumerate(SRC_DEFS, 1):
        if key == "lastpoll":
            s = None if perf_source == "w3" else lastpoll_status(lpst, lp_fresh_min if lp_fresh_min is not None else 35, now_hm or "12:00", lp_alert)
        elif key == "vmware":
            s = vmware_status(vmst, vm_configured, now_ymd, now_hm)
        else:
            s = rows_status(by_key.get(key) or [])
        if s:
            out.append({"no": idx, "key": key, "name": name, **s})
    return out


# ── 組卡片 ───────────────────────────────────────────────────────────
def _sources_now(conn, dc, run_rows: list[dict], now: datetime) -> list[dict]:
    """跟 /api/daily-check、/lastpoll、/vmware 三個端點同一批輸入；任何一個讀不到就把那個來源標「讀不到」（不當成正常）。"""
    cfg = dc.settings(conn)
    errs: dict[str, str] = {}
    lpst = vmst = None
    perf_source = cfg.get("daily_check_perf_source")
    if perf_source != "w3":
        try:
            import wug_lastpoll as lp
            lpst = lp.status_view(conn, cfg, now)
        except Exception as exc:  # noqa: BLE001 - 寫進卡片（標「讀不到」），不吞
            errs["lastpoll"] = type(exc).__name__
    try:
        import aria_dailycheck as ad
        vmst = ad.status_view(conn, now)
    except Exception as exc:  # noqa: BLE001
        errs["vmware"] = type(exc).__name__
    try:
        lp_alert = dc.today_status(conn, now).get("lp_alert")
    except Exception:  # noqa: BLE001 - 只是附註（today_status 自己也是這樣處理心跳）；讀不到就當沒有附註
        lp_alert = None
    srcs = build_sources(run_rows, lpst, vmst, perf_source, bool(cfg.get("vm_vcenters")), cfg.get("lp_fresh_min"),
                         now.strftime("%H:%M"), now.strftime("%Y-%m-%d"), lp_alert)
    names = [d[0] for d in SRC_DEFS]
    for k, e in errs.items():
        srcs = [s for s in srcs if s["key"] != k] + [{"no": names.index(k) + 1, "key": k, "name": dict(SRC_DEFS)[k],
                                                       "status": "讀不到", "at": "", "next": f"讀取失敗：{e}"}]
    return sorted(srcs, key=lambda s: s["no"])


def _night_line(conn, now: datetime) -> tuple[str, dict]:
    try:
        import night_desk
        v = night_desk.notified(conn, None, now)
        total = int(v["summary"]["total"])
        handled = sum(1 for x in v["items"] if x.get("handled"))
        return f"昨晚夜間通知：{total} 件，已有人處理 {handled} 件", {"night_total": total, "night_handled": handled}
    except Exception as exc:  # noqa: BLE001 - 讀不到就寫讀不到（不是 0），原因帶在卡片上
        return f"昨晚夜間通知：讀不到（{type(exc).__name__}）", {"night_error": type(exc).__name__}


def build(conn: sqlite3.Connection, cfg: dict, now: datetime, demo: bool = False) -> tuple[str, dict, dict]:
    """回 (標題, 卡片, 原始數字)。只讀。"""
    import daily_check as dc
    import notice_fmt as nf
    import teams_notify as tn

    day = now.strftime("%Y-%m-%d")
    link = tn._link(cfg, "/v2/dailycheck")
    demo_tag = "【示範】" if demo else ""
    title = f"{demo_tag}每日檢查日報 {day}"
    nums: dict = {"day": day}
    rid = dc.latest_run_id(conn, day)
    if rid is None:
        nums["run"] = None
        return title, tn._fit(nf.card(f"{demo_tag}今天還沒有產生每日檢查", [], link=link, color="Warning")), nums
    v = dc.run_view(conn, rid)
    rows, c = v["rows"], v["counts"]
    signoff = bool(dc.settings(conn).get("signoff"))
    submitted = bool(conn.execute("SELECT 1 FROM daily_check_run WHERE day = ? AND status = 'submitted' LIMIT 1", (day,)).fetchone())
    srcs = _sources_now(conn, dc, rows, now)
    ok_n = sum(1 for s in srcs if s["status"] == "正常")
    bad_rows = [r for r in rows if r["verdict"] == "bad"]
    unconfirmed = int(c.get("unconfirmed") or 0) if signoff else 0
    odd = [s for s in srcs if s["status"] != "正常"]
    nums.update({"run": rid, "total": c["total"], "bad": c["bad"], "nodata": c["nodata"], "sources_ok": ok_n, "sources_total": len(srcs),
                 "unconfirmed": unconfirmed, "submitted": submitted, "signoff": signoff})
    head = f"{demo_tag}今天 {c['total']} 項檢查：異常 {c['bad']}、無資料 {c['nodata']}・資料來源 {ok_n}／{len(srcs)} 正常"
    if signoff:
        head = (CONFIRM_WARN if not submitted else "已確認") + "｜" + head
    lines = nf.cap([f"{r['code']} {r['name']}：{(r.get('value_text') or r.get('reason') or '—')}" for r in bad_rows],
                   unit="項", limit=MAX_ABNORMAL_LINES)
    todo_parts = []
    if odd:
        todo_parts.append(f"資料來源 {len(odd)} 個不正常（{'、'.join(s['name'] + '：' + s['status'] for s in odd)}）")
    if unconfirmed:
        todo_parts.append(f"未確認 {unconfirmed} 列")
    nums["todo"] = len(odd) + (1 if unconfirmed else 0)
    lines.append("要你處理：" + ("；".join(todo_parts) if todo_parts else "沒有"))
    night, nn = _night_line(conn, now)
    nums.update(nn)
    lines.append(night)
    color = "Attention" if c["bad"] else ("Warning" if (odd or (signoff and not submitted)) else "Good")
    return title, tn._fit(nf.card(head, lines, link=link, color=color)), nums
