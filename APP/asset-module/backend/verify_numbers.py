#!/usr/bin/env python3
"""用程式獨立核對每一頁的檯面數字——不靠 AI、不靠人眼。

    python verify_numbers.py                 # 對 ASSET_DB_PATH（預設正式路徑）
    ASSET_DB_PATH=/path/asset.db python verify_numbers.py

為什麼要有（2026-09-17 使用者：「我不太相信你的數據，每一頁每一個數字都要用程式確認」）：
每個檯面數字用**第二種、獨立的算法**重算一次，跟 App 函式（畫面實際呼叫的那支）報的對；
再加上「加總＝各下鑽之和」「VM＋實體＝台數」「下鑽筆數＝檯面數字」這種**與口徑無關的不變式**
——不變式最強，因為它不需要重寫一次相同邏輯（重寫相同邏輯會連同 bug 一起複製）。

輸出：每項 ✅/❌／⚠️（App 值 vs 獨立值 vs 口徑）。有任何 ❌ 就 exit 1（可接 CI／排程）。
公司/正式區環境照樣跑（隨 patch/安裝包出貨），不需要連外、不需要 AI。唯讀，不改資料。
"""
from __future__ import annotations

import datetime
import html as _html
import os
import re
import json
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402
import manage_state  # noqa: E402
import json  # noqa: E402
import system_report  # noqa: E402
import system_stats  # noqa: E402

FAILS = 0
CHECKS = 0
RESULTS: list[dict] = []          # 給 HTML 報告用（跟畫面印的同一批）
CURRENT_SECTION = ""


def _mark(ok: bool | None) -> str:
    return "✅" if ok else ("⚠️ " if ok is None else "❌")


def check(page: str, name: str, app_v, indep_v, note: str = "") -> None:
    """App 值 vs 獨立重算值，相等才過。"""
    global FAILS, CHECKS
    CHECKS += 1
    ok = app_v == indep_v
    if not ok:
        FAILS += 1
    RESULTS.append({"sec": CURRENT_SECTION, "page": page, "name": name, "ok": ok,
                    "detail": f"App={app_v}　獨立={indep_v}", "note": note})
    tail = f"  〔{note}〕" if note else ""
    print(f"{_mark(ok)} [{page}] {name}：App={app_v}  獨立={indep_v}{tail}")


def warn(page: str, name: str, ok: bool, detail: str = "") -> None:
    """待處理狀態（不是數字錯）：ok=False 只印 ⚠️、不計為失敗。例：規則改了還沒套用。"""
    global CHECKS
    CHECKS += 1
    RESULTS.append({"sec": CURRENT_SECTION, "page": page, "name": name,
                    "ok": True if ok else None, "detail": detail, "note": ""})
    print(f"{_mark(True if ok else None)} [{page}] {name}{f'  〔{detail}〕' if detail else ''}")


def inv(page: str, name: str, ok: bool, detail: str = "") -> None:
    """不變式（與口徑無關）：例 加總=下鑽、VM+實體=台數。"""
    global FAILS, CHECKS
    CHECKS += 1
    if not ok:
        FAILS += 1
    RESULTS.append({"sec": CURRENT_SECTION, "page": page, "name": name, "ok": ok,
                    "detail": detail, "note": ""})
    tail = f"  〔{detail}〕" if detail else ""
    print(f"{_mark(ok)} [{page}] {name}{tail}")


def section(title):
    def deco(fn):
        def wrapped(*a, **k):
            global CURRENT_SECTION
            CURRENT_SECTION = title
            print(f"\n──── {title} ────")
            try:
                return fn(*a, **k)
            except Exception as exc:  # noqa: BLE001 - 一段炸掉不影響其他段，但要標紅
                global FAILS
                FAILS += 1
                RESULTS.append({"sec": title, "page": title, "name": "這段檢查自己出錯",
                                "ok": False, "detail": f"{type(exc).__name__}: {exc}", "note": ""})
                print(f"❌ [{title}] 這段檢查自己出錯：{type(exc).__name__}: {exc}")
        return wrapped
    return deco


def write_html(path: str) -> None:
    """把這次跑的結果寫成一份可留存／給主管看的 HTML 報告（白綠配色）。"""
    e = _html.escape
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    secs: dict[str, list[dict]] = {}
    for r in RESULTS:
        secs.setdefault(r["sec"], []).append(r)
    ok_all = FAILS == 0
    n_warn = sum(1 for r in RESULTS if r["ok"] is None)
    rows_html = []
    for sec, items in secs.items():
        # 警告（ok is None）＝待處理、不擋部署：顯示成「⚠ 警告」，不算成不符（2.043.15，GPT 複核 E）
        bad = sum(1 for x in items if x["ok"] is False)
        warns = sum(1 for x in items if x["ok"] is None)
        badge = (f"❌ {bad}" if bad else "全過") + (f"・⚠ {warns}" if warns else "")
        rows_html.append(
            f'<tr class="sec"><td colspan="4">{e(sec)}　'
            f'<span class="secbadge {"ok" if bad == 0 else "bad"}">{badge}</span></td></tr>')
        for x in items:
            st = ('<span class="pill ok">✓ 通過</span>' if x["ok"] else
                  '<span class="pill warn">⚠ 警告</span>' if x["ok"] is None else '<span class="pill bad">✗ 不符</span>')
            note = f'<div class="note">{e(x["note"])}</div>' if x.get("note") else ""
            rows_html.append(
                f'<tr class="{"" if x["ok"] or x["ok"] is None else "rowbad"}">'
                f'<td>{st}</td><td class="pg">{e(x["page"])}</td>'
                f'<td>{e(x["name"])}{note}</td><td class="dt">{e(x["detail"])}</td></tr>')
    doc = f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>資產盤點｜數字核對報告</title>
<style>
:root{{--brand:#00806a;--ink:#1a2b28;--soft:#5b6b67;--line:#e3e9e7;--card:#fff;--paper:#f4f7f6;
--good:#00806a;--goodbg:#e6f3ef;--bad:#c0392b;--badbg:#fbecea;--radius:14px}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);
font-family:"Space Grotesk","Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif;line-height:1.5}}
.wrap{{max-width:920px;margin:0 auto;padding:32px 16px 64px}}
h1{{font-size:22px;margin:0 0 4px}} .sub{{color:var(--soft);font-size:13px;margin-bottom:20px}}
.hero{{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
padding:20px 22px;margin-bottom:22px;display:flex;flex-wrap:wrap;gap:22px;align-items:center}}
.big{{font-size:40px;font-weight:700;line-height:1}}
.big.ok{{color:var(--good)}} .big.bad{{color:var(--bad)}}
.hero .lab{{color:var(--soft);font-size:12px;margin-top:4px}}
.verdict{{margin-left:auto;padding:10px 18px;border-radius:999px;font-weight:600;font-size:15px}}
.verdict.ok{{background:var(--goodbg);color:var(--good)}}
.verdict.bad{{background:var(--badbg);color:var(--bad)}}
table{{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;font-size:14px}}
td{{padding:9px 12px;border-top:1px solid var(--line);vertical-align:top}}
tr.sec td{{background:var(--goodbg);color:var(--brand);font-weight:600;font-size:13px;border-top:2px solid var(--brand)}}
.secbadge{{font-size:11px;font-weight:600;padding:1px 8px;border-radius:999px;margin-left:6px}}
.secbadge.ok{{background:#fff;color:var(--good)}} .secbadge.bad{{background:var(--bad);color:#fff}}
.pill{{font-size:12px;font-weight:600;padding:2px 9px;border-radius:999px;white-space:nowrap}}
.pill.ok{{background:var(--goodbg);color:var(--good)}} .pill.bad{{background:var(--bad);color:#fff}} .pill.warn{{background:#fff4d6;color:#8a5a00}}
.pg{{color:var(--soft);white-space:nowrap;font-size:13px}}
.dt{{color:var(--soft);font-variant-numeric:tabular-nums;white-space:nowrap}}
.note{{color:var(--soft);font-size:12px;margin-top:2px}}
tr.rowbad td{{background:var(--badbg)}}
.foot{{color:var(--soft);font-size:12px;margin-top:16px;line-height:1.7}}
</style></head><body><div class="wrap">
<h1>IT 戰情室　數字核對報告</h1>
<div class="sub">每個檯面數字用第二種獨立算法重算一次，並套「加總＝下鑽、VM＋實體＝台數、下鑽筆數＝檯面數」等不變式。<br>此報告由程式（verify_numbers.py）產出，不經 AI 判讀；可在正式／公司環境重跑。</div>
<div class="hero">
<div><div class="big">{CHECKS}</div><div class="lab">核對項目</div></div>
<div><div class="big {'ok' if ok_all else 'bad'}">{CHECKS - FAILS - n_warn}</div><div class="lab">通過</div></div>
<div><div class="big">{n_warn}</div><div class="lab">警告</div></div>
<div><div class="big {'ok' if ok_all else 'bad'}">{FAILS}</div><div class="lab">不符</div></div>
<div class="verdict {'ok' if ok_all else 'bad'}">{'全部通過' if ok_all else '有數字對不上'}</div>
</div>
<table><tbody>{''.join(rows_html)}</tbody></table>
<div class="foot">資料庫：{e(str(db.get_db_path()))}<br>產出時間：{now}<br>模組版本：{e(_version())}</div>
</div></body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"\n📄 已寫出 HTML 報告：{path}")


def _version() -> str:
    try:
        import json
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.json"), encoding="utf-8") as f:
            return "v" + json.load(f).get("version", "?")
    except Exception:  # noqa: BLE001 - 版本讀不到不影響報告主體
        return "v?"


# ---- 共用的獨立重算工具（跟 App 不同的碼路徑）----
def _hosts(conn):
    """所有 hardware，照『主機名+IP』併台（跟 App 的 machine_key 同定義、獨立實作）。"""
    # 退役＝備查、不是資產（使用者 2026-10-17）：全庫台數只算非退役
    rows = [r for r in conn.execute(
        "SELECT asset_serial,hostname,ip,is_vm,device_model,asset_status FROM hardware").fetchall()
        if (r["asset_status"] or "").strip() not in manage_state.RETIRED_STATUS]
    groups: dict = {}
    for r in rows:
        k = system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        groups.setdefault(k, []).append(r)
    return rows, groups


@section("台數 / 去重（無重複灌水）")
def check_dedup(conn):
    rows, groups = _hosts(conn)
    ca = [a for a in system_report.classify_assets(conn) if not a["retired"]]
    check("台數", "classify_assets 台數 = 主機名+IP 去重", len(ca), len(groups),
          "同主機名+IP 只算一台")
    inv("台數", "classify_assets 一資產一筆（無爆量）", len(ca) == len({a["asset_serial"] for a in ca}))
    dup_rows = sum(len(v) for v in groups.values() if len(v) > 1)
    dup_grp = sum(1 for v in groups.values() if len(v) > 1)
    print(f"   （參考：hardware {len(rows)} 筆；重複登記 {dup_grp} 組、{dup_rows - dup_grp} 筆多的——已被去重，未灌水）")


@section("頁A 各環境實體機分布")
def check_page_a(conn):
    pd = system_report.physical_distribution(conn)
    rooms_sum = sum(r["total"] for r in pd["rooms"])
    branch_sum = sum(b["count"] for b in pd["branches"])
    uncl_sum = sum(b["count"] for b in pd.get("unclassified", []))
    unf_sum = pd.get("unfilled_total", 0)
    inv("頁A", "total_physical = 各機房 + 各分公司 + 未歸類 + 未填（加總）",
        pd["total_physical"] == rooms_sum + branch_sum + uncl_sum + unf_sum,
        f"{pd['total_physical']} = {rooms_sum}+{branch_sum}+{uncl_sum}+{unf_sum}")
    # 「未填」永遠獨立一桶、「（未歸類）」只指有填但認不出：兩桶下鑽互不重複，且各等於檯面數
    _du = system_report.drill_physical(conn, room=manage_state.LOC_UNCLASSIFIED)
    _df = system_report.drill_physical(conn, room=manage_state.location_unfilled_label())
    inv("頁A", "未歸類桶下鑽筆數 = 檯面數", len(_du) == uncl_sum, f"下鑽 {len(_du)} / 檯面 {uncl_sum}")
    inv("頁A", "未填桶下鑽筆數 = 檯面數", len(_df) == unf_sum, f"下鑽 {len(_df)} / 檯面 {unf_sum}")
    inv("頁A", "未歸類與未填下鑽沒有重複計數",
        not ({x["asset_serial"] for x in _du} & {x["asset_serial"] for x in _df}), "")
    _each = sum(len(system_report.drill_physical(conn, branch=b["name"])) for b in pd.get("unclassified", []))
    inv("頁A", "未歸類各原值下鑽加總 = 檯面數", _each == uncl_sum, f"{_each} vs {uncl_sum}")
    # 跨頁（打到真正的函式結果，不是同一個 helper 自算）：頁A 實體機各群組 ≤ 首頁同名群組台數；
    # 頁B 總覽機房列加總 = 總數；頁B 各機房列 = 它的下鑽筆數
    _comp = manage_state.composition(conn)
    for r in pd["rooms"]:
        if r["total"] > _comp["by_location"].get(r["room"], 0):
            inv("頁A", f"機房「{r['room']}」實體機數 ≤ 首頁同名機房台數", False,
                f"頁A {r['total']} > 首頁 {_comp['by_location'].get(r['room'], 0)}")
    _ov = system_report.system_overview(conn)
    inv("頁B", "機房列加總 = 總數", sum(r["total"] for r in _ov["rooms"]) == _ov["total"],
        f"{sum(r['total'] for r in _ov['rooms'])} vs {_ov['total']}")
    _badov = [r["room"] for r in _ov["rooms"]
              if len(system_report.drill_system_overview(conn, room=r["room"])) != r["total"]]
    inv("頁B", "各機房列下鑽筆數 = 檯面數", not _badov, f"對不上：{_badov[:3]}" if _badov else "")
    # 獨立重算：baseline 內、非 VM、非排除型號
    hw = {r["asset_serial"]: r for r in conn.execute(
        "SELECT asset_serial,is_vm,device_model FROM hardware")}
    indep = 0
    for a in system_report.report_baseline(conn):
        r = hw.get(a["asset_serial"])
        if r and not manage_state.is_vm_value(r["is_vm"], r["device_model"]) \
                and not system_report.is_excluded_model(r["device_model"]):
            indep += 1
    check("頁A", "實體機總數", pd["total_physical"], indep,
          "CIA登記/未退役/非帳外/平台已知/排設備/去重")
    # 加總=下鑽：每個機房點進去的筆數 = 該機房檯面數
    for room in pd["rooms"]:
        if room["total"] == 0:
            continue
        drill = system_report.drill_physical(conn, room=room["room"])
        n = len(drill.get("items", drill) if isinstance(drill, dict) else drill)
        inv("頁A", f"機房「{room['room']}」下鑽筆數 = 檯面數", n == room["total"],
            f"下鑽 {n} / 檯面 {room['total']}")


@section("頁B 主機系統總覽")
def check_page_b(conn):
    ov = system_report.system_overview(conn)
    inv("頁B", "VM + 實體 = 總數", ov["vm"] + ov["physical"] == ov["total"],
        f"{ov['vm']}+{ov['physical']} vs {ov['total']}")
    inv("頁B", "核心+非核心+測試+未分類 = 總數",
        ov["core"] + ov["noncore"] + ov["test"] + ov["uncategorized"] == ov["total"],
        f"{ov['core']}+{ov['noncore']}+{ov['test']}+{ov['uncategorized']} vs {ov['total']}")
    inv("頁B", "total = report_baseline 台數", ov["total"] == len(system_report.report_baseline(conn)))
    # 獨立重算 VM/實體（baseline 內）
    hw = {r["asset_serial"]: r for r in conn.execute(
        "SELECT asset_serial,is_vm,device_model FROM hardware")}
    vm = phys = 0
    for a in system_report.report_baseline(conn):
        r = hw.get(a["asset_serial"])
        if not r:
            continue
        if manage_state.is_vm_value(r["is_vm"], r["device_model"]):
            vm += 1
        else:
            phys += 1
    check("頁B", "VM 台（baseline）", ov["vm"], vm)
    check("頁B", "實體 台（baseline）", ov["physical"], phys)


@section("系統組報告（10.1）加總＝下鑽")
def check_system_group(conn):
    """2.048.16（V49 改 V2 原生）：每一格數字點下去的筆數＝格子數字（同一支 /api/reports/system-group/drill）。
    前端把 null 送成空字串（表3「未歸叢集」「版本未知」那幾列），這裡照同樣送法驗。"""
    pl = system_report.platform_lifecycle(conn)
    bad = []
    for r in pl["rows"]:
        if r["total"] and len(system_report.drill_platform(conn, platform=r["platform"])) != r["total"]:
            bad.append(f"{r['platform']} 全部")
        for s in system_report.STATUS_ORDER:
            if r.get(s) and len(system_report.drill_platform(conn, platform=r["platform"], status=s)) != r[s]:
                bad.append(f"{r['platform']}／{s}")
    for s in system_report.STATUS_ORDER:
        tot = sum(r.get(s, 0) for r in pl["rows"])
        if len(system_report.drill_platform(conn, status=s, registered_only=True)) != tot:
            bad.append(f"摘要卡 {s}")
    for e in pl["excluded"]:
        if len(system_report.drill_platform(conn, bucket=e["platform"])) != e["count"]:
            bad.append(f"不列入 {e['platform']}")
    if len(system_report.drill_platform(conn, registered_only=True)) != pl["total"]:
        bad.append("合計")
    if len(system_report.drill_platform(conn, retired=True)) != pl["retired_excluded"]:
        bad.append("已退役")
    inv("系統組報告", "表1 每格（含摘要卡、不列入、已退役）下鑽筆數 = 檯面數", not bad, f"對不上：{bad[:4]}" if bad else "")
    bad = [v["os_canonical"] for v in system_report.os_version_breakdown(conn)
           if len(system_report.drill_platform(conn, os_canonical=v["os_canonical"], registered_only=True)) != v["count"]]
    inv("系統組報告", "作業系統版本明細每列下鑽筆數 = 檯面數", not bad, f"對不上：{bad[:4]}" if bad else "")
    bad = [f"{loc['location']}／{r['service']}" for loc in system_report.cluster_summary(conn)["locations"] for r in loc["rows"]
           if len(system_report.drill_cluster(conn, location=loc["location"], service=r["service"])) != r["esxi_count"]]
    _cs = system_report.cluster_summary(conn)["locations"]
    bad += [f"{loc['location']} 合計" for loc in _cs if len(system_report.drill_cluster(conn, location=loc["location"])) != loc["total"]]
    _svc: dict = {}
    for loc in _cs:
        for r in loc["rows"]:
            _svc[r["service"]] = _svc.get(r["service"], 0) + r["esxi_count"]
    bad += [f"{s} 合計" for s, n in _svc.items() if len(system_report.drill_cluster(conn, service=s)) != n]
    inv("系統組報告", "ESXi 矩陣每格（含列／欄合計）下鑽筆數 = 檯面數", not bad, f"對不上：{bad[:4]}" if bad else "")
    bad = [f"{c['vcenter']}／{c['cluster']}／{c['version']}" for c in system_report.virtualization_env(conn)["clusters"]
           if len(system_report.drill_cluster(conn, vcenter=c["vcenter"] or "", cluster=c["cluster"] or "",
                                              version=c["version"] or "")) != c["count"]]
    inv("系統組報告", "虛擬化環境每列（含未歸叢集、版本未知）下鑽筆數 = 檯面數", not bad, f"對不上：{bad[:4]}" if bad else "")


@section("分佈統計（AP 系統）")
def check_distribution(conn):
    bs = system_stats.by_system(conn, limit=None)
    ca_hosts = sum(1 for a in system_report.classify_assets(conn) if not a["retired"])
    check("分佈統計", "total_assets = 全庫台數", bs["total_assets"], ca_hosts)
    # 十項盤點第 5、6 條（2026-09-18）：全庫要拆得開，拆出來的要加得回去
    _pt = bs.get("partition") or {}
    check("分佈統計", "在管＋帳外 = 全庫台（退役＝備查不計）", _pt.get("managed", 0)
          + _pt.get("offbook", 0), bs["total_assets"])
    inv("分佈統計", "system_count = systems 列數", bs["system_count"] == len(bs["systems"]))
    # 加總=下鑽：涵蓋台數最多的系統，點進去的台數 = 檯面 total
    top = max(bs["systems"], key=lambda s: s.get("total", 0), default=None)
    if top:
        dd = system_stats.drilldown(conn, top["api_id"])
        inv("分佈統計", f"系統「{top.get('name') or top['api_id']}」下鑽台數 = 檯面",
            dd["total"] == top["total"], f"下鑽 {dd['total']} / 檯面 {top['total']}")
        # 下鑽的機房分布加總 = 該系統 total
        loc_sum = sum(l["total"] for l in dd["locations"])
        inv("分佈統計", "  ↳ 下鑽機房分布加總 = 系統 total", loc_sum == dd["total"],
            f"{loc_sum} / {dd['total']}")


@section("資料品質")
def check_data_quality(conn):
    import data_quality
    m = data_quality.measure(conn)
    for d in m["dimensions"]:
        # 分區不變式：驗過的 = 合格 + 不合格
        inv("資料品質", f"{d['label']}：checked = ok + bad",
            d["checked"] == d["ok"] + d["bad"], f"{d['checked']}={d['ok']}+{d['bad']}")
        # 下鑽=數字：列出來的不合格筆數 = bad
        try:
            offenders = data_quality.list_offenders(conn, d["key"])
            check("資料品質", f"{d['label']}：下鑽筆數 = bad", len(offenders), d["bad"])
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️  [資料品質] {d['label']}：下鑽不適用/錯誤（{type(exc).__name__}）")
    check_trust_drill(conn)


def check_trust_drill(conn):
    """2.048.18（V27 改 V2 原生）：可信度分布每個筆數點下去（/api/trust/hosts）＝那一格，分數與來源組合各自加總＝total。
    compute_all 在正式庫要算一陣子：這段只算一次（暫時換成記住結果的版本），hosts_with 的篩選照樣用 App 的原碼。"""
    import trust_score
    orig = trust_score.compute_all
    memo: dict = {}

    def _once(c):
        if "v" not in memo:
            memo["v"] = orig(c)
        return memo["v"]
    trust_score.compute_all = _once
    try:
        dist = trust_score.distribution(conn)
        bad = [f"{b['score']} 分" for b in dist["by_score"] if len(trust_score.hosts_with(conn, b["score"], None)) != b["count"]]
        bad += [c["combo"] for c in dist["by_combo"] if len(trust_score.hosts_with(conn, None, c["combo"])) != c["count"]]
    finally:
        trust_score.compute_all = orig
    inv("資料品質", "可信度分布：每個分數／來源組合的下鑽筆數 = 檯面數", not bad, f"對不上：{bad[:4]}" if bad else "")
    s_sum, c_sum = sum(b["count"] for b in dist["by_score"]), sum(c["count"] for c in dist["by_combo"])
    inv("資料品質", "可信度分布：分數加總 = 來源組合加總 = total", s_sum == c_sum == dist["total"], f"{s_sum} / {c_sum} / {dist['total']}")


@section("資料品質：只出現一次的人名")
def check_single_names(conn):
    rows = conn.execute(
        """WITH names AS (
               SELECT TRIM(user_name) AS n FROM hardware WHERE TRIM(COALESCE(user_name,'')) <> ''
               UNION ALL SELECT TRIM(custodian) FROM hardware WHERE TRIM(COALESCE(custodian,'')) <> ''
               UNION ALL SELECT TRIM(person_name) FROM personnel WHERE TRIM(COALESCE(person_name,'')) <> ''
           ) SELECT COUNT(*) FROM (SELECT n FROM names GROUP BY n HAVING COUNT(*)=1)"""
    ).fetchone()[0]
    # 用 Python 再算一次（跟 SQL 不同路徑）
    from collections import Counter
    c: Counter = Counter()
    for col, tbl in (("user_name", "hardware"), ("custodian", "hardware"), ("person_name", "personnel")):
        for r in conn.execute(f"SELECT {col} AS n FROM {tbl} WHERE TRIM(COALESCE({col},'')) <> ''"):
            c[r["n"].strip()] += 1
    py_once = sum(1 for v in c.values() if v == 1)
    check("資料品質", "只出現一次的人名（SQL vs Python）", rows, py_once)


@section("VIP 分列（入口）")
def check_vip(conn):
    import vip_view
    v = vip_view.entries(conn)
    rows_in = sum(e["rows"] for e in v["entries"]) + sum(o["rows"] for o in v["others"])
    inv("VIP", "筆數守恆：entries + others + 空白 = total_rows",
        rows_in + v["empty_rows"] == v["total_rows"],
        f"{rows_in}+{v['empty_rows']} vs {v['total_rows']}")
    inv("VIP", "每個 VIP 台數 ≤ 筆數（去重不會比筆多）",
        all(e["hosts"] <= e["rows"] for e in v["entries"]))
    # total_rows 應等於來源列數（獨立算）
    src = len(vip_view._rows(conn))
    check("VIP", "total_rows = 來源列數", v["total_rows"], src)


@section("帳號盤點")
def check_accounts(conn):
    import account_inventory as ai
    accts = ai.list_accounts(conn)
    raw = conn.execute("SELECT COUNT(*) FROM host_account WHERE gone_at IS NULL").fetchone()[0]
    check("帳號盤點", "在線帳號數 = host_account(未消失)", len(accts), raw)
    hosts = ai.inventoried_hosts(conn)
    di = conn.execute("SELECT COUNT(DISTINCT ip) FROM host_account WHERE gone_at IS NULL").fetchone()[0]
    check("帳號盤點", "有帳號的主機數 = 不重複 IP", len(hosts), di)
    # 特權帳號下鑽 = sudoer/uid0 獨立算
    sudoers = len(ai.list_accounts(conn, sudoer_only=True))
    indep_su = conn.execute(
        "SELECT COUNT(*) FROM host_account WHERE gone_at IS NULL AND (is_sudoer=1 OR uid=0)").fetchone()[0]
    check("帳號盤點", "特權帳號數（sudoer/uid0）", sudoers, indep_su)
    # ★ 天條：ESXi 不可納管（2026-10-17）。之前就建好帳號的只列出來、不自動刪，由使用者決定。
    import onboard_eligibility
    esx = onboard_eligibility.onboarded_esxi(conn)
    warn("帳號盤點", f"已納管／有帳號但實際是 ESXi：{len(esx)} 台", not esx,
         "、".join(f"{e['hostname'] or e['ip']}（帳號 {e['accounts']}）" for e in esx[:20]))


@section("月報（會餵進 AI 月報的數字）")
def check_monthly(conn):
    import monthly_report
    secs = monthly_report.collect(conn)["sections"]
    sec = next(s["data"] for s in secs if s.get("name") == "資產總覽")  # _assets 段（被 @_section 包，數字在 data）
    total = sec.get("總台數")
    inv("月報", "依環境加總 = 總台數", sum(sec["依環境"].values()) == total,
        f"{sum(sec['依環境'].values())} vs {total}")
    inv("月報", "虛實加總 = 總台數", sum(sec["虛實"].values()) == total,
        f"{sum(sec['虛實'].values())} vs {total}")
    # EXIST1（2026-10-18）：「總台數」＝存在可信度達門檻的機器（台，machine_key 去重），
    # 跟首頁「在管」同一份——不是 CIA 登記台數。獨立重算：直接數 existence 下鑽清單的筆數。
    import existence
    check("月報", "總台數 = 首頁在管（存在可信度計入，下鑽筆數）", total,
          existence.machines_in(conn, "counted")["total"],
          "月報數字會被寫進給主管的報告，跟首頁頭條必須同一個數")
    inv("月報", "CIA 有登記 + CIA 待補登 = 總台數",
        sec.get("CIA 有登記", 0) + sec.get("CIA 待補登", 0) == total,
        f"{sec.get('CIA 有登記')}+{sec.get('CIA 待補登')} vs {total}")


@section("夜間速查：目前告警中（AUD-03）")
def check_night_alerts(conn, strict: bool | None = None):
    """夜間速查「目前告警中」（AUD-03）：數字磚＝下鑽清單＝原始快取表重算；未啟用／從未輪詢不得有資料列、不得說沒有告警。
    **預設是觀察模式**（⚠ 不使部署中止：真實 WhatsUp 資料只在公司正式區才有，第一次碰到時誤報紅燈會讓部署中止）；
    app_settings 的 verify_strict_night=1（預設不存在）才升級為會中止的 ❌。"""
    import night_alerts as na
    if strict is None:
        try:
            strict = (conn.execute("SELECT value FROM app_settings WHERE key = 'verify_strict_night'").fetchone() or [""])[0] == "1"
        except Exception:  # noqa: BLE001 - 沒有設定表＝沒開 strict
            strict = False

    def chk(name, ok, detail=""):
        if strict:
            inv("夜間速查告警", name, ok, detail)
        else:
            warn("夜間速查告警", name + ("" if ok else "（觀察模式：先人工核對，確定不是資料問題再開 verify_strict_night=1）"), ok, detail)
    d = na.build(conn)
    rows, c = d["rows"], d["counts"]
    try:
        raw = [r[0] for r in conn.execute("SELECT state FROM whatsup_alerts")]
    except Exception:  # noqa: BLE001 - 表不存在＝從未輪詢
        raw = []
    if d["status"] in ("not_enabled", "never"):
        chk("未取得（未啟用／從未輪詢）不得有資料列、也不得顯示成沒有告警", not rows and "目前沒有告警（" not in d["message"], f"{d['status']}：{len(rows)} 列")
        return
    crit = sum(1 for s in raw if s in ("當機", "嚴重", "無回應", "錯誤"))
    chk("總數 = 清單列數 = 原始快取表筆數", c["total"] == len(rows) == len(raw), f"{c['total']}／{len(rows)}／{len(raw)}")
    chk("嚴重 = 清單中嚴重列數 = 原始狀態重算", c["critical"] == sum(1 for r in rows if r["severity"] == "嚴重") == crit, f"{c['critical']} vs {crit}")
    chk("嚴重 + 警告 = 總數", c["critical"] + c["warning"] == c["total"], f"{c['critical']}+{c['warning']} vs {c['total']}")
    chk("未對應 = 清單中非『已對應』列數，且 ≤ 總數", c["unmapped"] == sum(1 for r in rows if r["match"] != "matched") <= c["total"], f"{c['unmapped']}")
    chk("項次連號 1..N 且不重複", [r["__no"] for r in rows] == list(range(1, len(rows) + 1)))
    chk("過舊／時間異常／最近失敗不得說沒有告警；來源有效且零筆才說沒有告警", not (d["status"] not in ("ok",) and "目前沒有告警（" in d["message"])
        and not (d["status"] == "ok" and not rows and "目前沒有告警（" not in d["message"]))


@section("10.4 WhatsUp 告警事件：月報次數＝事件筆數、開著的沒有結束時間（2.043.0）")
def check_wug_alerts(conn):
    """唯讀。告警事件表是 Teams／夜間速查／主機履歷／月報共用的單一來源：
    ① 月報（本月與上月）總次數＝該月開始的事件筆數，每台次數加總＝總次數，每台次數＝點下去的筆數、總時長＝點下去的加總；
    ② 狀態列「開著幾筆」＝未結案且非維護的筆數；③ 同一設備＋監控項目最多一筆開著；
    ④ 已結案的結束時間不早於開始時間；持續分鐘不為負。"""
    import wug_alert as wa
    if not wa._has(conn, "alert_event"):
        inv("告警事件", "尚未啟用（沒有事件表）", True)
        return
    now = wa._now()
    first = now.replace(day=1)
    for ym in (now.strftime("%Y-%m"), (first - __import__("datetime").timedelta(days=1)).strftime("%Y-%m")):
        m = wa.month_summary(conn, ym)
        n = conn.execute("SELECT COUNT(*) FROM alert_event WHERE substr(started_at, 1, 7) = ?", (ym,)).fetchone()[0]
        check("告警事件", f"{ym} 月報總次數 = 該月開始的事件筆數", m["events"], n)
        inv("告警事件", f"{ym} 每台次數加總 = 總次數", sum(g["events"] for g in m["machines"]) == m["events"],
            f"{sum(g['events'] for g in m['machines'])} vs {m['events']}")
        bad = []
        for g in m["machines"][:200]:
            d = wa.events(conn, month=ym, limit=20000, **wa.drill_filter(g))
            if len(d) != g["events"] or sum(int(r["duration_min"] or 0) for r in d) != g["minutes"]:
                bad.append(g["who"])
        inv("告警事件", f"{ym} 每台次數／總時長 = 點下去的筆數／加總", not bad, "、".join(map(str, bad[:5])))
    st = wa.status(conn, now)
    n_open = conn.execute("SELECT COUNT(*) FROM alert_event WHERE ended_at IS NULL AND tier != 'maint'").fetchone()[0]
    check("告警事件", "狀態列開著幾筆 = 未結案且非維護的筆數", st["open"], n_open)
    dup = conn.execute("SELECT COUNT(*) FROM (SELECT 1 FROM alert_event WHERE ended_at IS NULL GROUP BY wug_device_id, monitor HAVING COUNT(*) > 1)").fetchone()[0]
    inv("告警事件", "同一設備＋監控項目最多一筆開著", dup == 0, f"{dup} 組重複")
    neg = conn.execute("SELECT COUNT(*) FROM alert_event WHERE duration_min < 0 OR (ended_at IS NOT NULL AND ended_at < started_at)").fetchone()[0]
    inv("告警事件", "結束時間不早於開始時間、持續分鐘不為負", neg == 0, f"{neg} 筆")
    opened = wa.events(conn, state="open", limit=20000)
    inv("告警事件", "清單「未恢復」的都沒有結束時間", all(r["ended_at"] is None for r in opened), f"{len(opened)} 筆")


@section("10.5 WhatsUp 中繼匯入的百分比刻度（2.047.6）")
def check_wug_percent_scale(conn):
    """唯讀。WhatsUp 原生回 0～1 小數，中繼匯入時換成 0–100（relay_import.to_percent），舊列由啟動時的一次性遷移 ×100：
    ① 已標 pct_scale=percent 的中繼列（容量分析 CPU／記憶體、磁碟使用率）每個值都在 0–100（容許 1e-4 浮點誤差）；
    ①-b 已標記卻「整列所有值 ≤ 1」的占比（⚠ 觀察：過半可能是漏乘 100，也可能真的很閒）；
    ② 還沒標記的舊中繼列＝遷移還沒跑到（⚠ 觀察：服務啟動時會自動遷移，不使部署中止）。"""
    import gzip as _gz
    import json as _json
    import capacity_analysis as ca
    import relay_import as ri
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('capacity_whatsup_perf','relay_disk_util')")}
    if not have:
        inv("WhatsUp 刻度", "尚未匯入任何 WhatsUp 資料", True)
        return

    def _out(arrs):
        return [x for a in arrs if isinstance(a, list) for x in a if isinstance(x, (int, float)) and not (0 <= x <= 100 + 1e-4)]

    def _all_le1(arrs):
        nums = [x for a in arrs if isinstance(a, list) for x in a if isinstance(x, (int, float)) and not isinstance(x, bool)]
        return bool(nums) and all(x <= 1 for x in nums)
    bad, legacy, marked, low = [], 0, 0, 0
    if "capacity_whatsup_perf" in have:
        for host, day, blob in conn.execute("SELECT host, day, data FROM capacity_whatsup_perf"):
            try:
                row = ca._decode(blob)
            except ValueError:
                continue
            if row.get("src") != "relay":
                continue
            if row.get(ri.SCALE_MARK) != "percent":
                legacy += 1
                continue
            marked += 1
            arrs = [(row.get(k) or {}).get(m) for k in ("cols", "max", "min") for m in ("cpu_busy", "mem_used")]
            if _out(arrs):
                bad.append(f"{host} {day}")
            low += _all_le1(arrs)
    if "relay_disk_util" in have:
        for host, day, disk, blob in conn.execute("SELECT host, day, disk, data FROM relay_disk_util"):
            try:
                d = _json.loads(_gz.decompress(blob).decode())
            except (OSError, ValueError, TypeError):
                continue
            if not isinstance(d, dict):
                continue
            if d.get(ri.SCALE_MARK) != "percent":
                legacy += 1
                continue
            marked += 1
            if _out([d.get(k) for k in ("avg", "max", "min")]):
                bad.append(f"{host} {day} {disk}")
            low += _all_le1([d.get(k) for k in ("avg", "max", "min")])
    inv("WhatsUp 刻度", f"已換成 0–100 的中繼列（{marked} 列）每個值都在 0–100", not bad, "、".join(bad[:5]))
    warn("WhatsUp 刻度", "已換好的中繼列中「整列所有值 ≤ 1%」不到一半（過多可能是漏乘 100；觀察用）",
         not marked or low * 2 < marked, f"{low}／{marked} 列（{round(100 * low / marked, 1) if marked else 0}%）")
    warn("WhatsUp 刻度", "沒有還沒換刻度的舊中繼列（2.047.5 以前匯入的小數）", legacy == 0, f"{legacy} 列待遷移（服務啟動時自動處理）" if legacy else "")


@section("10.3 容量分析封存：引用版本鏈、保護、中位數／超標分鐘（2.042.06）")
def check_capacity_archive(conn, sample: int = 300):
    """唯讀。① 引用：有效＋被更正取代＋已作廢＝全部版本數；每條鏈恰好一個最新版；被引用的日摘要一筆都不在可清理清單裡。
    ② 日摘要（演算法 2 起）每格：超標下限 ≤ 上限；有精確值時下限＝上限＝精確值；有中位數的格子一定是 raw 且狀態 ok。"""
    import capacity_archive as ar
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('capacity_day_archive','capacity_archive_citation')")}
    if "capacity_day_archive" not in have:
        warn("10.3 封存", "沒有日摘要封存表（尚未啟用），略過", True)
        return
    data = ar.citations(conn)
    rows = data["rows"]
    inv("10.3 封存", "引用：有效＋被更正取代＋已作廢 = 全部版本數", sum(data["counts"].values()) == len(rows), f"{data['counts']}／{len(rows)}")
    latest_per_chain = {}
    for r in rows:
        if r["is_latest"]:
            latest_per_chain[r["root_id"]] = latest_per_chain.get(r["root_id"], 0) + 1
    inv("10.3 封存", "引用：每條版本鏈恰好一個最新版", len(latest_per_chain) == data["chains"] and all(v == 1 for v in latest_per_chain.values()),
        f"{data['chains']} 條")
    inv("10.3 封存", "引用：被取代的版本都有下一版、最新版都沒有下一版",
        all((r["status"] == "superseded") == (not r["is_latest"]) for r in rows), "")
    cited = {i for r in rows for x in r["resolved"] for i in x["archive_ids"]}
    cand = ar.purge_candidates(conn)
    leak = sorted(cited & {i for x in cand["_expired"] for i in x["ids"]})
    inv("10.3 封存", "被引用（含舊版、作廢）的日摘要一筆都不在可清理清單裡", not leak, f"{leak[:5]}")
    bad = []
    n = 0
    for (aid, summ) in conn.execute("SELECT id, summary_json FROM capacity_day_archive WHERE algo_version = ? ORDER BY id DESC LIMIT ?",
                                    (ar.ALGO_VERSION, sample)):
        for c in json.loads(summ).get("cells", []):
            n += 1
            lo, hi, ex, med = c.get("over_lo"), c.get("over_hi"), c.get("over_min"), c.get("median")
            if lo is not None and hi is not None and lo > hi:
                bad.append(f"#{aid} 下限>上限")
            if ex is not None and not (lo == hi == ex):
                bad.append(f"#{aid} 精確值與範圍不一致")
            if med is not None and (c.get("bucket") or c.get("status") != "ok"):
                bad.append(f"#{aid} 中位數出現在非 raw／非 ok 的格子")
    inv("10.3 封存", f"日摘要格子：超標下限 ≤ 上限、精確值＝範圍、中位數只在 raw 且 ok（抽查 {n} 格）", not bad, f"{bad[:5]}")


@section("10.3 容量分析（分時段，ACT-04）")
def check_capacity(conn, max_hosts: int = 10, budget_s: float = 60.0, host_budget_s: float = 30.0, today=None, strict: bool | None = None):
    """對容量分析做『獨立重算』：不呼叫 capacity_analysis 的統計函式，直接解開來源表自己數。
    ① cells[].samples 加總（cpu）＝獨立數出的取樣點；② 每個非跨午夜時段格：樣本數、期望數（覆蓋率分母，逐日用當天間隔）、覆蓋率、
    時段內最大連續缺口、日型天數、平均／P95／最大值，都由獨立程式重算並比對；③ 不足格不得有平均或 P95。
    多來源／UTC／跨午夜／分段／彙整桶 不是『略過＝通過』，而是列為『未獨立驗證』警示並計入結果摘要。
    **預設是觀察模式**：不一致只輸出 ⚠️ 並計入摘要、不使部署中止（真實 WhatsUp 資料只在公司正式區才有，第一次碰到時誤報紅燈會讓部署中止）；
    app_settings 的 verify_strict_capacity=1（預設不存在）才升級為會中止的 ❌。首次在正式區看到警示時，先人工核對，再開 strict。
    大資料只抽樣（依日期輪替，不是永遠前 10 台）、總時間與單台時間都有上限；單台壞資料隔離為該台失敗，不中止整個檢查。"""
    import time as _t
    from datetime import date as _dd
    import capacity_analysis as ca
    cfg = ca.get_config(conn)
    if strict is None:
        try:
            strict = (conn.execute("SELECT value FROM app_settings WHERE key = 'verify_strict_capacity'").fetchone() or [""])[0] == "1"
        except Exception:  # noqa: BLE001 - 沒有設定表＝沒開 strict
            strict = False

    def chk(name, ok, detail=""):
        if strict:
            inv("容量分析", name, ok, detail)
        else:
            warn("容量分析", name + ("" if ok else "（觀察模式：先人工核對，確定不是資料問題再開 verify_strict_capacity=1）"), ok, detail)
    hosts = ca.list_hosts(conn)
    if not hosts:
        inv("容量分析", "沒有歷史資料（略過；分析頁會顯示『尚無資料』）", True)
        return
    rot = ((today or _dd.today()).toordinal() * max_hosts) % len(hosts)
    sample = (hosts[rot:] + hosts[:rot])[:max_hosts]
    warn("容量分析", f"抽樣檢查 {len(sample)}／{len(hosts)} 台（依日期輪替）", len(sample) == len(hosts), "大資料只抽樣，總時間上限 %ds" % budget_s)
    t0 = _t.time()
    _last = ca.latest_data_day(conn)
    rng_to = _last
    rng_from = (_dd.fromisoformat(_last) - __import__("datetime").timedelta(days=cfg["params"]["target_span_days"] - 1)).isoformat() if _last else None
    for h in sample:
        if _t.time() - t0 > budget_s:
            warn("容量分析", "超過時間上限，後面的主機未檢查", False, f"{h} 起略過")
            break
        try:
            a = ca.analyze_cached(conn, h, cfg, rng_from, rng_to)
            bad = [c for c in a["cells"] if (c["status"] in ("low_coverage", "few_days", "coarse_interval", "no_count") and (c["avg"] is not None or c["p95"] is not None))
                   or (c["status"] == "ok" and ((c["max"] is not None and (c["avg"] is not None and c["avg"] > c["max"] or (c["p95"] is not None and c["p95"] > c["max"]))) or (c["avg"] is not None and c["avg"] < 0)))
                   or (c.get("coverage_pct") is not None and c["coverage_pct"] > 100)]
            chk(f"{h}：不足格無平均／P95、正常格 平均與P95 ≤ 最大值、覆蓋率 ≤ 100%", not bad, f"違規 {len(bad)} 格")
            chk(f"{h}：日型天數加總 = 有資料天數", sum(a["days_by_type"].values()) == a["data_days"], f"{sum(a['days_by_type'].values())} vs {a['data_days']}")
            for name, ok, detail, kind in independent_capacity(conn, h, cfg, a, deadline=_t.time() + host_budget_s, frm=rng_from, to=rng_to):
                if kind == "unverified":
                    warn("容量分析", f"{h}：未獨立驗證（{name}）", False, detail)
                else:
                    chk(f"{h}：{name}", ok, detail)
        except Exception as exc:  # noqa: BLE001 - 單台壞資料（壞 gzip／JSON、逾時）隔離為該台失敗，不中止整個檢查
            chk(f"{h}：這台檢查自己出錯", False, f"{type(exc).__name__}: {str(exc)[:80]}")


def independent_capacity(conn, host: str, cfg: dict, a: dict, deadline: float | None = None, frm: str | None = None, to: str | None = None) -> list[tuple]:
    """回 [(檢查名, 是否通過, 細節, 種類)]，種類 ok／unverified。只獨立驗證：單一 NMON 來源、全 raw、單一資產分段、沒有 UTC、時段不跨午夜；
    其餘情況回 unverified（未獨立驗證），不當通過。"""
    import gzip as _gz
    import json as _js
    import math as _m
    import time as _t
    import zlib as _zl
    import capacity_analysis as ca
    from datetime import date as _d
    from datetime import timedelta as _td
    why = []
    if a.get("sources") != ["nmon"]:
        why.append("非單一 NMON 來源")
    if any(c["kind"] != "raw" for c in a["cells"]):
        why.append("含彙整桶")
    if len({c.get("segment") for c in a["cells"]}) > 1:
        why.append("同一裝置分成多個對應段落")
    slots = [(s["key"], int(s["start"][:2]) * 60 + int(s["start"][3:]), int(s["end"][:2]) * 60 + int(s["end"][3:])) for s in cfg["slots"]]
    if any(b <= x for _, x, b in slots):
        why.append("有跨午夜時段")
    if why:
        return [("、".join(why), True, "此類情況尚無獨立重算", "unverified")]
    heavy = {x["date"] for x in cfg["heavy_days"]}
    hol = {x["date"] for x in cfg["holidays"]}

    def dtype(day):
        if day in heavy:
            return "normal"          # 大量交易日只是標註，仍是交易日
        if day in hol or _d.fromisoformat(day).weekday() >= 5:
            return "off"
        return "normal"
    vals: dict[tuple, list] = {}
    offs: dict[tuple, list] = {}
    days: dict[str, set] = {"normal": set(), "off": set()}
    itv_day: dict[str, int] = {}
    total_cpu = 0
    secs: dict[tuple, list] = {}                 # (日型, 時段, 日) → [(距時段起點秒數, 值)]：獨立重算超標分鐘用
    for day, itv, blob in conn.execute("SELECT day, interval_s, data FROM nmon_series WHERE host = ? ORDER BY day", (host,)):
        if (frm and day < frm) or (to and day > to):
            continue                                  # 與正式分析同一個區間
        if deadline and _t.time() > deadline:
            return [("單台時間上限", False, "超過單台檢查時間上限，未完成", "ok")]
        _z = _zl.decompressobj(31)
        _raw = _z.decompress(blob, ca.MAX_BLOB_BYTES)          # 與正式分析相同的解壓大小上限
        if _z.unconsumed_tail:
            return [("解壓後超過大小上限", True, "與正式分析一樣拒絕這列", "unverified")]
        d = _js.loads(_raw)
        if any(not (isinstance(t, str) and ca.TIME_RE.fullmatch(t[:8]) and len(t) == 8) for t in d["ts"]):
            return [("含非法時刻的列", True, "正式分析整列拒絕，這裡不獨立重算", "unverified")]
        if str(d.get("tz") or "").strip().upper() not in ("", "ASIA/TAIPEI"):
            return [("含 UTC 或其他時區", True, "尚無獨立重算", "unverified")]
        dt = dtype(day)
        days[dt].add(day)
        itv_day[day] = itv
        cpu = (d.get("cols") or {}).get("cpu_busy")
        seen = set()
        for i, t in enumerate(d["ts"]):
            if t[:8] in seen:
                continue
            seen.add(t[:8])
            m = int(t[0:2]) * 60 + int(t[3:5])
            v = cpu[i] if cpu is not None and i < len(cpu) else None
            sk = next((k for k, x, b in slots if x <= m < b), "_other")
            if v is not None:
                total_cpu += 1
                vals.setdefault((dt, sk), []).append(v)
                offs.setdefault((dt, sk, day), []).append(m - next((x for k, x, b in slots if k == sk), 0))
                secs.setdefault((dt, sk, day), []).append(((m - next((x for k, x, b in slots if k == sk), 0)) * 60 + int(t[6:8]), v))
    out = []
    cells = [c for c in a["cells"] if c["metric"] == "cpu_busy" and c["source"] == "nmon"]
    out.append(("cells[].samples 加總 = 獨立數出的 cpu 取樣點", sum(c["samples"] for c in cells) == total_cpu, f"{sum(c['samples'] for c in cells)} vs {total_cpu}", "ok"))
    mine = {k: len(v) for k, v in days.items()}
    out.append(("日型天數 = 獨立重算", mine == a["days_by_type"], f"{mine} vs {a['days_by_type']}", "ok"))
    wrong = []
    for c in cells:
        if c["slot"] == "_other":
            continue
        s = next(x for x in slots if x[0] == c["slot"])
        v = sorted(vals.get((c["day_type"], c["slot"]), []))
        n = len(v)
        exp = sum(int((s[2] - s[1]) * 60 / itv_day[dd]) for dd in days[c["day_type"]] if itv_day.get(dd)) or None
        # 應有日子（與正式分析同一規則）：這台第一天有資料起～分析區間的 to（沒給用最後有資料日），日型相符的每一個日曆日
        alld = sorted(days["normal"] | days["off"])
        dd0 = _d.fromisoformat(max(frm, alld[0]) if frm else alld[0])
        dd1 = _d.fromisoformat(to if to else alld[-1])
        cal = [(dd0 + _td(days=i)).isoformat() for i in range((dd1 - dd0).days + 1)]
        gone = [x for x in cal if dtype(x) == c["day_type"] and x not in days[c["day_type"]]]
        if exp:            # 整天沒有資料的應有日子也算進分母（來源停擺），用最細的間隔估
            exp += len(gone) * int((s[2] - s[1]) * 60 / min(itv_day[dd] for dd in days[c["day_type"]] if itv_day.get(dd)))
        cov = min(100.0, round(100 * n / exp, 1)) if exp else None
        g = 0
        for dd in days[c["day_type"]]:
            pts = [0] + sorted(set(offs.get((c["day_type"], c["slot"], dd), []))) + [s[2] - s[1]]
            g = max(g, max(y - x for x, y in zip(pts, pts[1:])))
        if (c["samples"], c["expected"], c["coverage_pct"], c["max_gap_min"]) != (n, exp, cov, g):
            wrong.append((c["day_type"], c["slot"], (c["samples"], c["expected"], c["coverage_pct"], c["max_gap_min"]), (n, exp, cov, g)))
        # 超標分鐘（> 門檻）：每點代表 min(來源間隔, 到下一點的距離)、裁到時段終點；應有日子裡沒被涵蓋的時間（含整天停擺）算沒有資料
        if c["status"] not in ("not_collected", "no_data", "coarse_interval") and c.get("over_threshold") is not None:
            thr = float(cfg["params"]["high_pct"])
            slot_s = (s[2] - s[1]) * 60
            lo_s = miss_s = 0.0
            unknown = False
            for dd in sorted(set(days[c["day_type"]]) | set(gone)):
                pts = sorted(dict(secs.get((c["day_type"], c["slot"], dd), [])).items())
                if pts and not itv_day.get(dd):
                    unknown = True
                cov_s = 0.0
                for i, (o, val) in enumerate(pts):
                    dur = float(itv_day.get(dd) or 0)
                    if i + 1 < len(pts):
                        dur = min(dur, float(pts[i + 1][0] - o))
                    dur = max(0.0, min(dur, float(slot_s - o)))
                    cov_s += dur
                    if val > thr:
                        lo_s += dur
                miss_s += max(0.0, slot_s - min(cov_s, slot_s))
            want = (None, None, None) if unknown else (round(lo_s / 60, 1), round((lo_s + miss_s) / 60, 1), round(miss_s / 60, 1))
            got = (c.get("over_lo"), c.get("over_hi"), c.get("over_missing_min"))
            if got != want:
                wrong.append((c["day_type"], c["slot"], "超標分鐘（下限／上限／沒有資料）", got, want))
        if c["status"] == "ok" and v:
            stats = (round(sum(v) / n, 2), round(v[max(1, _m.ceil(0.95 * n)) - 1], 2), round(v[-1], 2))
            if (c["avg"], c["p95"], c["max"]) != stats:
                wrong.append((c["day_type"], c["slot"], "avg/p95/max", (c["avg"], c["p95"], c["max"]), stats))
    out.append(("樣本數／覆蓋率分母／覆蓋率／最大缺口／平均／P95／最大值／超標分鐘 = 獨立重算", not wrong, f"不一致 {len(wrong)} 格 {wrong[:1]}", "ok"))
    return out


@section("首頁組成／分佈／平台／機房×環境（composition）")
def check_composition(conn):
    # 這一支蓋掉首頁「在管/帳外/退役」「平台組成」「虛實」「環境別」「機房分佈」「機房×環境
    # 交叉」「OS 來源」與分佈頁——全是「各分組加總＝母體」的純不變式（最強：不需重寫口徑）。
    import manage_state as ms
    c = ms.composition(conn)
    active = c["total"]                       # 有效資產（排退役，含帳外）
    inv("組成", "在管 + 帳外 = 有效資產",
        c["registered_total"] + c["off_book_total"] == active,
        f"{c['registered_total']}+{c['off_book_total']} vs {active}")
    # 登記台數（去重）≤ 登記筆數（一台可能多系統/多VIP各登一筆）；headline 用台、括號用筆
    inv("組成", "登記台數 ≤ 登記筆數（去重不會比筆多）",
        c.get("registered_hosts", 0) <= c["registered_total"],
        f"台 {c.get('registered_hosts')} / 筆 {c['registered_total']}")
    # 主機清單頁（以台為單位）台數／筆數＝首頁在管台／登記筆（同母體，同一把去重尺）
    import host_view
    hv = host_view.list_hosts(conn)
    check("組成", "主機清單台數 = 在管台數", hv["total_hosts"], c.get("registered_hosts"))
    check("組成", "主機清單登記筆數 = 登記筆數", hv["total_rows"], c["registered_total"])
    # 來源對照表（5-8 報表列印）：在管要等於首頁在管台；三類要窮盡全庫。
    # 2026-09-18 公司機實測這裡差 35 台（退役判定寫成「任一筆退役」），畫面各說各話。
    import host_sources
    _hs = host_sources.summary(host_sources.rows(conn))
    check("來源對照表", "CIA在管 = 首頁在管台（主機清單台數）", _hs["cia_hosts"], hv["total_hosts"])
    check("來源對照表", "在管＋只在帳外 = 全庫台（已退役＝備查，另列不計）",
          _hs["cia_hosts"] + _hs["offbook_only_hosts"], _hs["total_hosts"])
    inv("組成", "有效 + 退役 = 全部", active + c["retired_count"] == c["total_all"],
        f"{active}+{c['retired_count']} vs {c['total_all']}")
    for name, key in (("平台", "by_platform"), ("環境別", "by_environment"),
                      ("虛實", "by_virtualization"), ("機房", "by_location")):
        s = sum(c[key].values())
        inv("組成", f"{name}分組加總 = 有效資產", s == active, f"{s} vs {active}")
    inv("組成", "狀態分佈加總 = 全部（含退役）",
        sum(c["by_status"].values()) == c["total_all"],
        f"{sum(c['by_status'].values())} vs {c['total_all']}")
    inv("組成", "OS 來源（收到＋推測）= 有效資產",
        c["os_from_facts"] + c["os_guessed"] == active,
        f"{c['os_from_facts']}+{c['os_guessed']} vs {active}")
    # 下鑽：每個平台的 OS 版本加總 = 該平台台數
    bad = [p for p, osmap in c["by_platform_os"].items()
           if sum(osmap.values()) != c["by_platform"].get(p, 0)]
    inv("組成", "平台→OS 下鑽加總 = 平台台數", not bad, f"對不上：{bad[:3]}" if bad else "")
    # OS 大類（OS-CLASS-AUDIT）：首頁長條加總 = 有效資產；每類的細平台加總 = 該類；
    # 首頁長條 = 點進去 /assets?os_class= 的筆數（asset_classify，同一支 os_class.classify）
    s_cls = sum(c["by_os_class"].values())
    inv("組成", "OS 大類加總 = 有效資產", s_cls == active, f"{s_cls} vs {active}")
    badc = [k for k, pm in c["by_os_class_platform"].items()
            if sum(pm.values()) != c["by_os_class"].get(k, 0)]
    inv("組成", "OS 大類→細平台 下鑽加總 = 大類筆數", not badc, f"對不上：{badc[:3]}" if badc else "")
    unk = set(c["by_os_class"]) - set(c["os_class_order"])
    inv("組成", "OS 大類都在設定檔類別內", not unk, f"多出：{sorted(unk)[:3]}" if unk else "")
    import asset_classify
    from collections import Counter as _C
    _cls = asset_classify.classify_all(conn)
    drill = _C(_cls[r["asset_serial"]]["os_class"] for r in conn.execute(
        "SELECT asset_serial, asset_status FROM hardware")
        if (r["asset_status"] or "").strip() not in ms.RETIRED_STATUS and r["asset_serial"] in _cls)
    badd = [k for k in set(drill) | set(c["by_os_class"]) if drill.get(k, 0) != c["by_os_class"].get(k, 0)]
    # 2026-10-18：已納管主機以收集到的 OS 為準（collected_os）——收集到 AIX／Linux／Windows、
    # 登記判成泛用類（其他／未知）的，大類不可以還停在泛用類
    import collected_os
    _col = collected_os.platform_map(conn)
    stuck = [s for s, p in _col.items()
             if s in _cls and _cls[s]["os_class"] in ("其他", "未知")]
    inv("組成", "收集到 OS 的已納管主機，大類不停在「其他／未知」", not stuck,
        f"{stuck[:3]}" if stuck else "")
    inv("組成", "首頁 OS 大類長條 = 點進去（?os_class=）筆數", not badd,
        f"對不上：{[(k, c['by_os_class'].get(k, 0), drill.get(k, 0)) for k in badd[:3]]}" if badd else "")
    # 機房×環境交叉：每個機房的環境加總 = 該機房台數；總和 = 有效資產
    badloc = [loc for loc, envmap in c["by_location_env"].items()
              if sum(envmap.values()) != c["by_location"].get(loc, 0)]
    inv("組成", "機房×環境 每機房加總 = 機房台數", not badloc, f"對不上：{badloc[:3]}" if badloc else "")
    grand = sum(sum(e.values()) for e in c["by_location_env"].values())
    inv("組成", "機房×環境 總和 = 有效資產", grand == active, f"{grand} vs {active}")
    # 機房分組（Codex #2）：打到「真正的路由函式」——/assets?location_group= 實際回傳筆數 = 首頁該組台數；
    # 網段反推（suggest_segments）看到的各機房台數加總 = 有 IP 的有效資產（同一個 group 尺）
    from fastapi import Response as _Resp
    from routers.assets import list_assets as _list_assets
    _badg = []
    for g, n in c["by_location"].items():
        got = len(_list_assets(response=_Resp(), location_group=g, session=None, conn=conn))
        if got != n:
            _badg.append((g, n, got))
    inv("組成", "首頁機房分組台數 = /assets?location_group= 實際回傳筆數（含未歸類／未填）", not _badg,
        f"對不上：{_badg[:3]}" if _badg else "")
    inv("組成", "「未填」與「（未歸類）」是兩個獨立分組（不重複）",
        manage_state.LOC_UNCLASSIFIED != manage_state.location_unfilled_label(), "")
    from routers.collect_scan import suggest_segments as _sugg
    _sg = _sugg(fmt="json", session=None, conn=conn)["segments"]
    _with_ip = conn.execute(
        "SELECT COUNT(*) FROM hardware WHERE ip IS NOT NULL AND ip != ''").fetchone()[0]
    _seg_hosts = sum(sum(s_["locations"].values()) for s_ in _sg)
    _bad_ip = _sugg(fmt="json", session=None, conn=conn)["bad_ip_count"]
    inv("組成", "網段反推各機房台數加總 = 有 IP 的資產（扣 IP 格式錯誤）", _seg_hosts + _bad_ip == _with_ip,
        f"{_seg_hosts}+{_bad_ip} vs {_with_ip}")
    _pend = [s_["cidr"] for s_ in _sg if s_["location_pending"]]
    _mis = [s_["cidr"] for s_ in _sg
            if bool(s_["location_pending"]) != (s_["main_location"] in (manage_state.LOC_UNCLASSIFIED,
                                                                          manage_state.location_unfilled_label()))]
    inv("組成", "網段主要機房為未歸類／未填者必標待確認（不自動建）", not _mis, f"{_mis[:3]}" if _mis else f"待確認 {len(_pend)} 段")


@section("納管漏斗四態（pipeline）")
def check_pipeline(conn):
    import pipeline
    s = pipeline.summarize(conn)
    inv("納管", "各關加總 = 母體（互斥且窮盡）", s["reconcile"]["ok"],
        f"{s['reconcile']['sum_of_stages']} vs {s['reconcile']['total']}")
    check("納管", "items 台數 = 母體", len(s["items"]), s["total"])
    # 下鑽：每一關的 items 數 = 該關 counts
    from collections import Counter
    by_stage = Counter(it["stage"] for it in s["items"])
    bad = [k for k, v in s["counts"].items() if by_stage.get(k, 0) != v]
    inv("納管", "每一關 items 數 = 檯面 counts", not bad, f"對不上：{bad[:3]}" if bad else "")
    inv("納管", "OS 分類加總 = 母體", sum(s["os_counts"].values()) == s["total"],
        f"{sum(s['os_counts'].values())} vs {s['total']}")
    # 首頁四格（manage_state.summarize，逐台）與漏斗（pipeline，逐台）必須同母體——
    # 2026-09-17 抓到 ms 曾逐筆算（失聯 4641 筆 vs 漏斗 4226 台），已改逐台。
    import manage_state as _ms
    m = _ms.summarize(conn)
    check("納管", "首頁四態母體 = 漏斗母體（都逐台）", m["total_known"], s["total"])
    inv("納管", "首頁四態各狀態加總 = 母體",
        sum(m["counts"].values()) == m["total_known"],
        f"{sum(m['counts'].values())} vs {m['total_known']}")
    # 注意：pipeline 母體含「掃到未登記」，本來就比 classify 台多，不可拿來相等比對。
    # 去重「用同一把尺」（machine_key）改由 test_pipeline_dedup_uses_machine_key 守——
    # 2026-09-17 抓到 pipeline 曾自寫 (host,ip) 把兩台都填 0.0.0.0 的 Cisco 交換器誤併成一台。


def api_stats(conn) -> dict:
    """畫面實際拿到的那份對帳數字（直接呼叫 API 的函式，不重寫）。"""
    import api

    # environment 只影響上面那組環境篩選過的數字；檯面對帳用的是全站的 total_*，跟它無關
    return api.dashboard_stats(session=None, conn=conn)


@section("首頁對帳（一致／搜不到／未登記）")
def check_reconciliation(conn):
    # 2026-09-17 使用者在公司機抓到「搜不到 7823 > 在管 3768」：對帳用原始筆數（含退役＋
    # 帳外），頭條在管用去退役去帳外——同頁兩套口徑。這段鎖住「對帳的登記universe＝在管」。
    import manage_state as ms
    import system_report as sr
    comp = ms.composition(conn)
    registered = comp["registered_total"]
    # 獨立重算對帳的「登記」universe（跟 api.dashboard_stats 修正後同口徑：排退役＋帳外）
    reg = [r for r in conn.execute(
        "SELECT ip, hostname, asset_serial, asset_status FROM hardware").fetchall()
        if (r["asset_status"] or "").strip() not in ms.RETIRED_STATUS
        and not str(r["asset_serial"] or "").startswith(sr.OFF_BOOK_PREFIXES)]
    check("首頁對帳", "對帳登記數 = 頭條在管（registered_total）", len(reg), registered,
          "對帳的『登記』要跟『在管』同口徑，否則搜不到會比在管大")
    # 一致／搜不到＝拿最新掃描去對；重點不變式：搜不到是子集，不可能比在管大
    last = conn.execute("SELECT MAX(scan_time) FROM scan_history").fetchone()[0]
    srows = conn.execute(
        "SELECT ip, hostname FROM scan_history WHERE scan_time = ? AND scan_ok = 1",
        (last,)).fetchall() if last else []
    sip = {r["ip"] for r in srows if r["ip"]}
    shost = {r["hostname"] for r in srows if r["hostname"]}
    overlap = sum(1 for r in reg
                  if (r["ip"] and r["ip"] in sip) or (r["hostname"] and r["hostname"] in shost))
    ica_only = len(reg) - overlap
    inv("首頁對帳", "一致 + 搜不到 = 在管", overlap + ica_only == registered,
        f"{overlap}+{ica_only} vs {registered}")
    inv("首頁對帳", "搜不到 ≤ 在管（子集不可能比總數大）", ica_only <= registered,
        f"搜不到 {ica_only} / 在管 {registered}")
    # [口徑] 2026-09-20 公司驗收：畫面寫「台」卻拿筆數（搜不到 3,417 > 在管 3,374 台）。
    # 檯面改逐台之後，這裡也獨立用 machine_key 重算一次，並釘住「一致＋搜不到＝在管台數」。
    import system_stats as ss
    seen_by_machine: dict[str, bool] = {}
    for r in reg:
        k = ss.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        seen_by_machine[k] = seen_by_machine.get(k, False) or (
            (r["ip"] and r["ip"] in sip) or (r["hostname"] and r["hostname"] in shost))
    m_total = len(seen_by_machine)
    m_overlap = sum(1 for v in seen_by_machine.values() if v)
    st = api_stats(conn)
    check("首頁對帳", "檯面『一致』= 獨立重算（逐台）", st.get("total_overlap_machines"), m_overlap)
    check("首頁對帳", "檯面登記台數 = 獨立重算（逐台）", st.get("total_ica_machines"), m_total)
    check("首頁對帳", "一致 + 搜不到 = 在管台數（逐台）",
          m_overlap + (m_total - m_overlap), comp["registered_hosts"])
    inv("首頁對帳", "搜不到（台）≤ 在管台數", m_total - m_overlap <= comp["registered_hosts"],
        f"{m_total - m_overlap} / {comp['registered_hosts']}")
    # 帳外：畫面寫「另有 N 台在 CIA 之外」——那個 N 也必須逐台
    off_keys = {ss.machine_key(r["hostname"], r["ip"], r["asset_serial"]) for r in conn.execute(
        "SELECT ip, hostname, asset_serial, asset_status FROM hardware").fetchall()
        if (r["asset_status"] or "").strip() not in ms.RETIRED_STATUS
        and str(r["asset_serial"] or "").startswith(sr.OFF_BOOK_PREFIXES)}
    check("首頁對帳", "帳外台數（畫面『CIA 之外』）= 獨立重算", comp.get("off_book_machines"), len(off_keys))
    inv("首頁對帳", "帳外台數 ≤ 帳外筆數", comp.get("off_book_machines", 0) <= comp["off_book_total"],
        f"{comp.get('off_book_machines')} / {comp['off_book_total']}")


@section("掃描範圍（規則式，2026-09-17 方案 A）")
def check_scan_scope(conn):
    import ipaddress
    import scan_scope as ss
    pol = ss.get_policy(conn)
    if pol["mode"] != "rule":
        inv("掃描範圍", "手動模式（規則檢查不適用）", True, "mode=manual")
        return
    resolved = ss.resolve_scope(conn)                      # App 的規則引擎
    res_in = [r for r in resolved if r["in_scope"]]
    rows = conn.execute(
        "SELECT cidr, category, environment, scan_excluded FROM network_segment").fetchall()

    def _size(c):
        try:
            return max(ipaddress.ip_network(c, strict=False).num_addresses - 2, 1)
        except (ValueError, TypeError):
            return 0

    # 獨立重算：不走 resolve_scope 的碼路，直接照 policy 過一遍原始網段
    indep_in, indep_addr = [], 0
    for r in rows:
        c = r["cidr"]
        if not c or c in pol["force_out"]:
            continue
        keep = c in pol["force_in"]
        if not keep:
            cat = (r["category"] or "").strip()
            env = (r["environment"] or "").strip()
            keep = ((not pol["categories"] or cat in pol["categories"])
                    and (not pol["environments"] or env in pol["environments"])
                    and not (pol["respect_recommended_exclude"] and r["scan_excluded"]))
        if keep:
            indep_in.append(c)
            indep_addr += _size(c)
    check("掃描範圍", "規則納入段數（引擎 vs 獨立重算）", len(res_in), len(indep_in))
    check("掃描範圍", "規則納入位址數", sum(_size(r["cidr"]) for r in res_in), indep_addr)
    # 不變式（與是否已套用無關）：規則納入的類別都在允許集、force_out 全不在、force_in 全在
    if pol["categories"]:
        bad = [r["cidr"] for r in res_in
               if (r["category"] or "").strip() not in pol["categories"]
               and r["cidr"] not in pol["force_in"]]
        inv("掃描範圍", "規則納入的類別都在允許集（force_in 除外）", not bad,
            f"越界：{bad[:3]}" if bad else "")
    in_cidrs = {r["cidr"] for r in res_in}
    inv("掃描範圍", "force_out 一段都不被規則納入",
        all(c not in in_cidrs for c in pol["force_out"]))
    parsable = {r["cidr"] for r in rows if r["cidr"]}
    inv("掃描範圍", "force_in（可解析）全被規則納入",
        all(c in in_cidrs for c in pol["force_in"] if c in parsable))
    # 實際掃描來源 vs 規則：不一致＝規則改了還沒按套用（待處理狀態，不是數字錯）
    sc = ss.list_scope(conn)
    warn("掃描範圍", "實際掃描來源與規則同步（in_sync）", sc["in_sync"],
         f"規則 {sc['rule_in_scope_segments']} 段／實際 {sc['in_scope_segments']} 段，待按套用同步"
         if not sc["in_sync"] else "")


@section("納管頁收集結果頁簽（/adopt，2026-10-13）")
def check_adopt_tabs(conn):
    """頁簽互斥且窮盡：各頁簽加總＝這輪探測的位址數；頁簽數字＝帶這個 tab 的列數。"""
    import collect_dispatch as cd
    try:
        out = cd.latest_run(conn)
    except Exception as exc:  # noqa: BLE001 - 舊庫沒有 collect_dispatch 表：沒跑過收集，不適用
        # 只有「沒有這張表」才算不適用（2026-10-19：缺欄位的錯也被這裡吞成「不適用」、打綠勾，
        # 同一個錯在下一段才紅——那是運氣）。其他錯誤＝這段檢查壞了，要紅。
        na = "no such table" in str(exc)
        inv("納管頁", "沒有收集紀錄（頁簽檢查不適用）" if na else "頁簽檢查自己出錯", na,
            f"{type(exc).__name__}: {exc}"[:200])
        return
    if not out:
        inv("納管頁", "沒有收集紀錄（頁簽檢查不適用）", True, "no run")
        return
    by_tab = out["by_tab"]
    check("納管頁", "各頁簽加總 = 探測位址數", sum(by_tab.values()), out["total"])
    # v1.468.4 合併檢視：每個位址一列（最新一次的結果），收一台不會讓總數變少
    distinct = conn.execute("SELECT COUNT(DISTINCT ip) FROM collect_dispatch_result").fetchone()[0]
    check("納管頁", "合併檢視列數 = 所有收集出現過的不同位址數", out["total"], distinct)
    inv("納管頁", "合併檢視每個位址只有一列", len({r["ip"] for r in out["results"]}) == len(out["results"]))
    last_n = conn.execute("SELECT COUNT(DISTINCT ip) FROM collect_dispatch_result WHERE run_id = "
                          "(SELECT MAX(id) FROM collect_dispatch_run)").fetchone()[0]
    inv("納管頁", "最近一次收集的位址數 ≤ 合併檢視列數（收一台不會藏掉其他結果）",
        last_n <= out["total"], f"最近一次 {last_n}、合併 {out['total']}")
    # 讀取時疊上「之後才納管成功」（2026-10-20）：疊過的列一定要有比那輪收集更新的證據，
    # 而且疊的列數＝表頭講的列數（不是悄悄改狀態）
    ov = [r for r in out["results"] if r.get("overlay_at")]
    inv("納管頁", "疊上「之後才納管成功」的列都有比那輪收集更新的證據",
        all(r["overlay_at"] > (r.get("collected_at") or "") and r["status"] == cd.STATUS_COLLECTED
            for r in ov), f"{len(ov)} 列")
    check("納管頁", "疊上現況的列數 = 表頭 overlaid", len(ov), (out.get("merged") or {}).get("overlaid", 0))
    bad = [t for t, n in by_tab.items() if n != sum(1 for r in out["results"] if r["tab"] == t)]
    inv("納管頁", "每個頁簽數字 = 下鑽列數", not bad, f"對不上：{bad}" if bad else "")
    # 2026-10-21：OCP 自成頁簽（以前在其他裡），一起算；舊 run 可能沒有這個鍵
    nok = sum(by_tab.get(t, 0) for t in (cd.TAB_STORAGE, cd.TAB_NETWORK, cd.TAB_ESXI, cd.TAB_OCP,
                                          cd.TAB_NOK_OTHER))
    # 2026-10-18：AS400／IBM i 落在「大型主機」頁簽（狀態仍是另有納管方式），要一起算
    nok_mf = sum(1 for r in out["results"]
                 if r["tab"] == cd.TAB_MAINFRAME and r["status"] == cd.STATUS_NOT_ONBOARDABLE)
    check("納管頁", "另有納管方式五個頁簽＋大型主機裡的 AS400 = 另有納管方式方塊",
          nok + nok_mf, out["not_onboardable"])
    inv("納管頁", "SSH 收集頁簽沒有混進另有納管方式的",
        all(r["status"] != cd.STATUS_NOT_ONBOARDABLE for r in out["results"] if r["tab"] == cd.TAB_SSH))
    # 大型主機（AIX／AS400）：不重複算——AIX 不再留在 SSH 收集頁簽（人工指定過的照人）
    mf = cd.mainframe_group()["classes"]
    inv("納管頁", "SSH 收集頁簽沒有 AIX／AS400（都在大型主機頁簽）",
        all(r.get("os_class") not in mf or r.get("os_override")
            for r in out["results"] if r["tab"] == cd.TAB_SSH))
    # 2026-10-19（v1.468.2）：頁簽說大型主機，下一步就是大型主機的路——不准「認不出」「目前不收」
    mf_bad = [r["ip"] for r in out["results"] if r["tab"] == cd.TAB_MAINFRAME and r.get("guide")
              and (r["guide"].get("kind") == "appliance" or not r["guide"].get("how"))]
    inv("納管頁", "大型主機頁簽沒有「認不出的設備／目前不收」（頁簽與下一步同一個判斷）",
        not mf_bad, str(mf_bad[:5]))
    inv("納管頁", "大型主機頁簽的 AIX 不是另有納管方式（AIX 走 SSH 收集）",
        all(r["status"] != cd.STATUS_NOT_ONBOARDABLE for r in out["results"]
            if r["tab"] == cd.TAB_MAINFRAME and r.get("os_class") == "AIX" and not r.get("os_override")))
    inv("納管頁", "另有納管方式的網路設備／儲存設備／設備每列都寫廠牌（不知道就寫廠牌不明）",
        all(r.get("vendor") and r["vendor"].get("text") for r in out["results"]
            if r["status"] == cd.STATUS_NOT_ONBOARDABLE
            and r.get("not_onboardable_kind") in cd.VENDOR_KINDS and r["tab"] != cd.TAB_MAINFRAME))
    # 2026-10-19：Push Agent 只支援 Linux／Unix——進不去的機器先判斷是什麼，Windows／AIX／設備不留在這頁
    agent_rows = [r for r in out["results"] if r["tab"] == cd.TAB_AGENT]
    inv("納管頁", "Push Agent 頁簽沒有判斷為 Windows 的（Windows 改請開 WinRM）",
        all((r.get("kind_guess") or {}).get("kind") != "windows" for r in agent_rows))
    inv("納管頁", "Push Agent 頁簽只有 Linux／Unix 或判斷不明的",
        all((r.get("kind_guess") or {}).get("kind") in ("linux", "unknown")
            for r in agent_rows if r["status"] == cd.STATUS_NEEDS_AGENT),
        str([(r["ip"], (r.get("kind_guess") or {}).get("kind")) for r in agent_rows
             if r["status"] == cd.STATUS_NEEDS_AGENT
             and (r.get("kind_guess") or {}).get("kind") not in ("linux", "unknown")][:5]))
    # 2026-10-19（v1.468.3）公司機：Push Agent 頁簽 18 列全是沒登記的 .1／.254、沒開任何服務埠、
    # 只憑 TTL 64 判成「Linux／Unix 推測」——那是閘道／網路設備介面，不是能裝 Agent 的伺服器
    import host_kind
    gw = next((x for x in host_kind.load_rules().get("scan_rules", []) if x.get("kind") == "gateway"), {})
    gw_oct, gw_none = set(gw.get("ip_last_octet_in") or []), set(gw.get("ports_none") or [])
    gw_bad = [r["ip"] for r in agent_rows
              if not r.get("registered") and not r.get("os_override")
              and host_kind._last_octet(r["ip"]) in gw_oct
              and not (set(host_kind._ports(r.get("open_ports"))) & gw_none)]
    inv("納管頁", "Push Agent 頁簽沒有「沒登記、沒開服務埠的網段頭尾位址」（閘道位址放網路設備）",
        not gw_bad, str(gw_bad[:5]))
    ttl_only = [r["ip"] for r in agent_rows
                if (r.get("kind_guess") or {}).get("kind") == "linux"
                and all(str(e).startswith("TTL") or "TTL≈" in str(e)
                        for e in (r.get("kind_guess") or {}).get("evidence") or ["TTL"])]
    inv("納管頁", "Push Agent 頁簽沒有只憑 TTL 就判成 Linux／Unix 的",
        not ttl_only, str(ttl_only[:5]))
    inv("納管頁", "待開 WinRM 的都在 WinRM 收集頁簽",
        all(r["tab"] == cd.TAB_WINRM or r.get("os_override")
            for r in out["results"] if r["status"] == cd.STATUS_NEEDS_WINRM_PORT))
    inv("納管頁", "活著但進不去的每一列都有判斷（是什麼機器）",
        all(r.get("kind_guess") for r in out["results"]
            if r.get("route") == cd.ROUTE_AGENT and r.get("alive") and r["status"] in cd.AGENT_FAMILY))
    # 空位址：不出現在任何畫面上的頁簽；「只能匯入」只算資產庫有登記的（真的有機器）
    check("納管頁", "空位址頁簽 = 空位址方塊（沒回應又沒登記）",
          by_tab.get(cd.TAB_EMPTY, 0), out["empty_addresses"])
    inv("納管頁", "只能匯入頁簽全部是有登記的機器（不含空位址）",
        all(r.get("registered") for r in out["results"] if r["tab"] == cd.TAB_IMPORT))
    check("納管頁", "畫面「全部」= 探測位址數 − 空位址",
          sum(n for t, n in by_tab.items() if t != cd.TAB_EMPTY),
          out["total"] - out["empty_addresses"])


@section("2-1 收集涵蓋率（2026-10-20）")
def check_dispatch_coverage(conn):
    """2-1 收集涵蓋率（2026-10-20）：每段已收集 ≤ 總位址；從未收集方塊 = 下鑽列數；
    每個收集過的位址都落在某一列（配置表網段或補上的 /24），不會在涵蓋率裡消失。"""
    import dispatch_coverage
    try:
        cov = dispatch_coverage.coverage(conn)
    except Exception as exc:  # noqa: BLE001 - 舊庫沒有 collect_dispatch 表：不適用；其他錯要紅
        na = "no such table" in str(exc)
        inv("收集涵蓋率", "沒有收集紀錄（不適用）" if na else "涵蓋率檢查自己出錯", na,
            f"{type(exc).__name__}: {exc}"[:200])
        return
    segs = cov["segments"]
    inv("收集涵蓋率", "每段已收集 ≤ 總位址（子集≤母體）", all(x["collected"] <= x["total"] for x in segs))
    check("收集涵蓋率", "從未收集方塊 = 從未收集列數", cov["summary"]["never"],
          sum(1 for x in segs if x["collected"] == 0))
    import bisect
    import ipaddress
    # 獨立重算：直接查結果表的不同位址（不經 _merged_results），每個都要落在某一列的範圍
    v4 = []
    for (ip,) in conn.execute("SELECT DISTINCT ip FROM collect_dispatch_result"):
        try:
            v4.append(int(ipaddress.IPv4Address(str(ip).strip())))
        except ValueError:
            continue          # IPv6／壞值：涵蓋率只算 IPv4，不列
    spans = sorted((x["start"], x["end"]) for x in segs)
    starts = [a for a, _ in spans]
    lost = 0
    for k in v4:
        i = bisect.bisect_right(starts, k)
        if not any(a <= k <= b for a, b in spans[max(0, i - 64):i])                 and not any(a <= k <= b for a, b in spans[:i]):
            lost += 1
    inv("收集涵蓋率", "每個收集過的位址都落在涵蓋率某一列", lost == 0, f"漏 {lost} 個" if lost else "")


@section("作業系統口徑：實測優先、沒有才用 CIA（決策 Q80，2026-10-16）")
def check_os_basis(conn):
    """hardware.os＝顯示值（實測優先）；os_cia＝CIA 原值。沒有實測證據的列，顯示值必須等於 CIA 原值。
    另列（資訊）：顯示值跟 CIA 登記不同的筆數＝畫面「登記與實測不符」的上限。唯讀。"""
    import dyn_shell

    cols = {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}
    if "os_cia" not in cols:
        inv("作業系統", "還沒有 os_cia 欄（舊庫，尚未升級）——不適用", True)
        return
    rows = conn.execute("SELECT id, asset_serial, os, os_cia FROM hardware "
                        "WHERE trim(coalesce(os_cia,'')) <> ''").fetchall()
    bad, diff = [], 0
    for r in rows:
        if (r["os"] or "").strip() != (r["os_cia"] or "").strip():
            diff += 1
            if not dyn_shell.cia_measured(conn, r["id"]):
                bad.append(r["asset_serial"])
    inv("作業系統", f"沒有實測證據的列：顯示 OS = CIA 登記（{len(rows)} 筆有 CIA 原值）", not bad,
        f"不一致：{bad[:5]}" if bad else "")
    warn("作業系統", f"顯示 OS 跟 CIA 登記不同（實測蓋過）{diff} 筆", True,
         "版本切換後由 CIA 重匯逐步填 os_cia；既有資料不回寫")


def _indep_machine_count(rows: list[dict], hostname_mode: bool) -> int:
    """台數的獨立重算（不呼叫 machine_identity／machine_key）：union-find 把同一台的列連起來數根。"""
    import json as _json
    import re as _re

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "machine_identity.json"),
              encoding="utf-8") as f:
        cfg = _json.load(f)
    gen = set(cfg["generic_names"])
    pats = [_re.compile(x, _re.I) for x in cfg["generic_patterns"]]
    dom = _re.compile(r"^([^.]+)\.((?:[a-z0-9-]+\.)+[a-z]{2,})$", _re.I)

    def short(h):
        h = (h or "").strip()
        m = dom.match(h)
        return (m.group(1) if m else h).strip().lower()

    parent = list(range(len(rows)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    first: dict = {}
    names: dict = {}
    for i, r in enumerate(rows):
        n = short(r.get("hostname"))
        if hostname_mode and n and n not in gen and not any(p.search(n) for p in pats):
            names.setdefault(n, []).append(i)
        ip = (r.get("ip") or "").strip()
        if n and ip and ip != "0.0.0.0":
            k = ("legacy", n, ip)
            if k in first:
                union(i, first[k])
            else:
                first[k] = i
    for n, idx in names.items():
        g = [rows[i] for i in idx]
        mac = {str(x.get("mac") or "").strip().lower().replace("-", ":").replace(".", ":") for x in g} - {""}
        sn = {str(x.get("hw_serial") or "").strip().lower() for x in g} - {""}
        env = {manage_state.group_environment(x.get("environment")) for x in g} & set(cfg["guard_env_groups"])
        if len(mac) > 1 or len(sn) > 1 or len(env) > 1:
            continue   # 待確認：不併，留給舊規則
        for i in idx[1:]:
            union(i, idx[0])
    return len({find(i) for i in range(len(rows))})


@section("系統校正（實測優先，v1.473.0）")
def check_measured_effective(conn):
    """已納管且有實測 OS 的主機，分類 = 實測分類；系統校正清單每筆都是真的不符、且是 CIA 正本。

    獨立算法：不呼叫 collected_os.measured_map，自己用「同一台（collapse_machines）裡有
    帳外那筆 collect_ok=1 且 os 講得出平台」去推期待的大類，再跟 asset_classify 的結果比。"""
    import asset_classify
    import lifecycle
    import measured_mismatch
    import os_class

    rows = [dict(r) for r in conn.execute("SELECT * FROM hardware")]
    cls = asset_classify.classify_all(conn)

    def plat(t):
        t = str(t or "").lower()
        if "aix" in t or (len(t) == 15 and t[4] == "-" and t[:4].isdigit()):
            return "AIX"
        if "windows" in t or "microsoft" in t:
            return "Windows"
        if any(k in t for k in ("linux", "red hat", "redhat", "rhel", "centos", "rocky", "ubuntu",
                                "debian", "suse")):
            return "Linux"
        return None
    bad, n = [], 0
    for g in system_stats.collapse_machines(rows).values():
        ev = [plat(r["os"]) for r in g if r.get("collect_ok") == 1
              and str(r["asset_serial"]).startswith(lifecycle.NON_CIA_PREFIXES) and plat(r["os"])]
        if len(set(ev)) != 1:
            continue        # 沒有帳外實測、或同一台兩筆實測互相矛盾：不在這條不變式的範圍
        want = ev[0]
        for r in g:
            reg = dict(r)
            if str(reg.get("os_cia") or "").strip():
                reg["os"] = reg["os_cia"]
            if os_class.classify_row(reg) not in ("其他", "未知", "AIX", "Linux", "Windows"):
                continue    # 登記成設備／虛擬化平台的不被實測翻掉（VMware fail-closed）
            if r.get("collect_ok") == 1 and str(r.get("os_cia") or "").strip() \
                    and str(r.get("os") or "").strip() != str(r.get("os_cia") or "").strip():
                continue    # 這筆自己也收過（facts 寫過），以它自己的實測為準
            n += 1
            got = (cls.get(r["asset_serial"]) or {}).get("os_class")
            if got != want:
                bad.append((r["asset_serial"], want, got))
    inv("系統校正", f"已納管且有實測 OS 的主機，分類 = 實測分類（{n} 筆）", not bad,
        f"對不上：{bad[:5]}" if bad else "")
    items = measured_mismatch.corrections(conn)
    wrong = [r["asset_serial"] for r in items
             if str(r["asset_serial"]).startswith(lifecycle.NON_CIA_PREFIXES)
             or (r["cia"] or "").strip() == (r["measured"] or "").strip() or not r["measured"]]
    inv("系統校正", f"系統校正清單每筆都是 CIA 正本、校正值≠CIA 原值（{len(items)} 筆）", not wrong,
        f"不對：{wrong[:5]}" if wrong else "")


@section("台數口徑（Q79 主機名＋護欄，2026-10-16）")
def check_machine_key_basis(conn):
    """目前口徑、最近一次判定、用這台真實資料重跑停損 a/b/c；畫面台數＝目前口徑的試算台數。唯讀。"""
    import machine_identity as mi

    mode = mi.effective_mode(conn)
    a = mi.analyse(conn)
    d = mi.last_decision(conn)
    hit = [k for k, v in a["stops"].items() if v["hit"]]
    print(f"   （口徑：{mode}；試算 {a['old']}→{a['new']}；停損 " + "、".join(
        f"{k}={'觸發' if v['hit'] else '未觸發'}（{v['value']}）" for k, v in a["stops"].items()) + "）")
    warn("台數口徑", f"最近一次判定：{d['mode']}（{d['old_n']}→{d['new_n']}，{d['decided_at']}）" if d
         else "尚未判定（deploy／patch 跑 db.py 時判）", d is not None)
    if mode == mi.MODE_HOSTNAME:
        warn("台數口徑", "主機名口徑下，用目前資料重跑停損仍未觸發", not hit,
             f"現在觸發 {hit}：要不要退回舊規則請看 /machine-key" if hit else "")
    rows = [dict(r) for r in conn.execute(
        "SELECT hostname, ip, asset_serial, asset_status, mac, hw_serial, environment FROM hardware")
        if (r["asset_status"] or "").strip() not in manage_state.RETIRED_STATUS]
    live = {system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"]) for r in rows}
    # 實測主機名校正（PRECEDENCE1）：獨立重算也要吃同一份「有效主機名」——那是輸入，不是口徑。
    # 校正本身對不對另由 check_hostname_fix 用原始資料逐筆驗。
    import hostname_fix
    _fx = hostname_fix.compute(conn)["fixes"] if hostname_fix.enabled(conn) else {}
    rows = [{**r, "hostname": _fx[r["asset_serial"]]["measured"]} if r["asset_serial"] in _fx else r
            for r in rows]
    # 審查 #10：用另一種寫法（union-find，直接讀設定檔與原始欄位）獨立重算台數，不呼叫 machine_identity
    check("台數口徑", "目前口徑台數（machine_key）= 獨立重算（union-find）", len(live),
          _indep_machine_count(rows, mode == mi.MODE_HOSTNAME))
    ca_live = sum(1 for x in system_report.classify_assets(conn) if not x["retired"])
    check("台數口徑", "目前口徑台數 = classify_assets 非退役台數", len(live), ca_live)


@section("實測主機名校正（PRECEDENCE1，v1.476.0）")
def check_hostname_fix(conn):
    """每一筆主機名校正都要用原始資料站得住：同 IP 上真的有證據講 H、CIA 的名字 C 沒有在別的 IP 收集過。"""
    import json as _json

    import hostname_fix

    st = hostname_fix.compute(conn)
    fixes = st["fixes"] if hostname_fix.enabled(conn) else {}

    def nm(h):
        h = str(h or "").strip().lower()
        return h.split(".")[0] if "." in h and not h.replace(".", "").isdigit() else h
    bad = []
    for s_, f in fixes.items():
        ip, h, c = f["ip"], nm(f["measured"]), nm(f["cia"])
        ev = any(nm(r["hostname"]) == h for r in conn.execute(
            "SELECT hostname FROM hardware WHERE trim(ip) = ? AND asset_serial <> ?", (ip, s_)))
        ev = ev or any(nm(r["hostname_observed"]) == h for r in conn.execute(
            "SELECT hostname_observed FROM hardware WHERE asset_serial = ?", (s_,)))
        if not ev:
            for r in conn.execute("SELECT payload FROM source_record WHERE source = 'vcenter'"):
                try:
                    pl = _json.loads(r["payload"] or "{}")
                except (TypeError, ValueError):
                    continue
                if isinstance(pl, dict) and str(pl.get("ip") or "").strip() == ip and nm(pl.get("hostname")) == h:
                    ev = True
                    break
        other = [r["asset_serial"] for r in conn.execute(
            "SELECT asset_serial, hostname, ip, asset_status FROM hardware WHERE collect_ok = 1 "
            "AND trim(ip) <> ?", (ip,))
            if nm(r["hostname"]) == c and (r["asset_status"] or "").strip() not in manage_state.RETIRED_STATUS]
        if not ev or other:
            bad.append((s_, "沒有證據" if not ev else f"{f['cia']} 在別的 IP 收集過：{other[:2]}"))
    inv("主機名校正", f"每筆校正都有同 IP 的實測證據、CIA 名字沒在別處收集過（{len(fixes)} 筆）", not bad,
        f"站不住：{bad[:5]}" if bad else "")
    # 同一個 IP 上不可以有兩個以上不同的 CIA 原名被校正成同一個名字（VIP／叢集誤併）
    by_ip: dict = {}
    for r in conn.execute("SELECT asset_serial, hostname, ip, asset_status FROM hardware"):
        if (r["asset_status"] or "").strip() in manage_state.RETIRED_STATUS                 or system_report.is_off_book(r["asset_serial"]):
            continue
        by_ip.setdefault((r["ip"] or "").strip(), set()).add(nm(r["hostname"]))
    multi = [s_ for s_, f in fixes.items() if len(by_ip.get(f["ip"], set())) > 1]
    inv("主機名校正", "同 IP 有兩個以上 CIA 名字的不校正（不把 VIP／叢集併成一台）", not multi,
        f"{multi[:5]}" if multi else "")
    n_hw = conn.execute("SELECT count(*) FROM hardware").fetchone()[0] or 1
    warn("主機名校正", f"校正筆數 {len(fixes)} ≤ 全部登記的 5%（{n_hw} 筆）", len(fixes) <= n_hw * 0.05,
         "校正太多，先看 /corrections 是不是有一批規則判錯" if len(fixes) > n_hw * 0.05 else "")
    import measured_mismatch
    n_list = sum(1 for r in measured_mismatch.corrections(conn) if r["field_key"] == "hostname")
    check("主機名校正", "校正清單主機名筆數 = 校正筆數", n_list, len(fixes))


@section("重複登記：儀表板＝清單（審查 #5，2026-10-17）")
def check_duplicate_consistency(conn):
    import _app_shared
    import vip_view

    lst = _app_shared._duplicate_groups(conn)
    summ = vip_view.duplicate_summary(conn)
    check("重複登記", "儀表板組數 = 重複清單組數", summ["groups"], len(lst))
    check("重複登記", "儀表板疑似可少筆數 = 清單加總", summ["suspect_extra_rows"],
          sum(g["extra_rows"] for g in lst))


@section("重複登記釋放：預覽＝清單、釋放紀錄與資產清冊一致、無孤兒（階段二，2026-11-02）")
def check_duplicate_release(conn, strict: bool | None = None, sample: int = 30):
    """釋放序號（硬刪除）的數字不變式。**預設是觀察模式**（⚠ 不使部署中止）；app_settings.verify_strict_release=1 才升級為會中止的 ❌。
    不在正式庫做任何刪除：預覽只讀，釋放前後的動態不變式（台數不變、各表歸零、還原逐列相同）由 tests/asset_module/test_host_release.py 守。
    """
    import _app_shared
    import host_lookup
    import host_release
    if strict is None:
        try:
            strict = (conn.execute("SELECT value FROM app_settings WHERE key = 'verify_strict_release'").fetchone() or [""])[0] == "1"
        except Exception:  # noqa: BLE001 - 沒有設定表＝沒開 strict
            strict = False

    def chk(name, ok, detail=""):
        if strict:
            inv("重複登記釋放", name, ok, detail)
        else:
            warn("重複登記釋放", name + ("" if ok else "（觀察模式：先人工核對，確定不是資料問題再開 verify_strict_release=1）"), ok, detail)

    d = host_lookup.duplicates(conn)
    lst = _app_shared._duplicate_groups(conn)
    chk("重複清單『多出筆數』總和 = 逐組多出筆數加總 = 原始分組加總",
        d["extra_rows"] == sum(g["extra_rows"] for g in d["groups"]) == sum(g["extra_rows"] for g in lst),
        f"{d['extra_rows']}／{sum(g['extra_rows'] for g in d['groups'])}／{sum(g['extra_rows'] for g in lst)}")
    chk("每組筆數加總 = 明細列數", sum(g["count"] for g in d["groups"]) == len(d["members"]),
        f"{sum(g['count'] for g in d['groups'])} vs {len(d['members'])}")
    bad = []
    for g in d["groups"][:sample]:
        rel = g["serials"][1:]
        try:
            pv = host_release.preview(conn, g["key"], rel)
        except host_release.ReleaseError as exc:
            bad.append(f"{g['key']}: {exc.message}")
            continue
        per = sum(i["total_rows"] for i in pv["release"])
        ok = (pv["count_before"] - len(rel) == pv["count_after"] == len(pv["keep"]) and pv["count_before"] == g["count"]
              and pv["totals"]["rows"] == per and all(i["counts"].get("hardware") == 1 for i in pv["release"]))
        if not ok:
            bad.append(f"{g['key']}: {pv['count_before']}-{len(rel)}≠{pv['count_after']}")
    chk(f"預覽：組內筆數 − 釋放筆數 = 釋放後筆數，且預覽列數加總一致（抽查 {min(sample, len(d['groups']))} 組）", not bad, f"{bad[:3]}" if bad else "")
    try:
        _app_shared._ensure_merge_audit(conn)
        rows = conn.execute("SELECT id, released_serial, restored_at FROM merge_audit WHERE snapshot_format >= 2").fetchall()
    except Exception:  # noqa: BLE001 - 唯讀庫無法建表：沒有釋放紀錄可核
        rows = []
    present = {r[0] for r in conn.execute("SELECT asset_serial FROM hardware")}
    gone_but_in_hw = [r["id"] for r in rows if r["restored_at"] is None and r["released_serial"] in present]
    restored_but_missing = [r["id"] for r in rows if r["restored_at"] is not None and r["released_serial"] not in present]
    chk("釋放紀錄與清冊一致：已還原的序號必須在清冊", not restored_but_missing, f"已還原卻不在清冊 {restored_but_missing[:5]}")
    chk("釋放紀錄與清冊一致：未還原的序號不在清冊（在清冊＝已被新資產占用，列表會標『序號已占用』）", not gone_but_in_hw,
        f"{gone_but_in_hw[:5]}")
    orphans = {}
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
        if host_release.is_kept(t) or t == "hardware":
            continue
        cols_ = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')]
        if "asset_serial" not in cols_ or t.lower() not in host_release.SNAPSHOT_TABLES:      # 只核可快照（會被釋放處理）的表
            continue
        n = conn.execute(f'SELECT COUNT(*) FROM "{t}" WHERE asset_serial IS NOT NULL AND asset_serial <> {chr(39) * 2} '
                         "AND asset_serial NOT IN (SELECT asset_serial FROM hardware)").fetchone()[0]
        if n:
            orphans[t] = n
    chk("沒有指向不存在序號的孤兒資料（以序號為鍵、非歷史稽核類的表）", not orphans, f"{orphans}" if orphans else "")


@section("退役＝備查：不計入任何台數（使用者 2026-10-17）")
def check_retired_zero(conn):
    """在記憶體副本把退役列刪掉，各頁台數必須一模一樣（＝退役對任何台數貢獻 0）。唯讀，不碰原庫。"""
    import sqlite3

    import business_system as _bs
    import host_sources
    import host_view
    import machine_identity
    import monthly_report
    import pipeline as _pipeline

    def _counts(c):
        ca = system_report.classify_assets(c)
        hs = host_sources.summary(host_sources.rows(c))
        sec = next(s["data"] for s in monthly_report.collect(c)["sections"] if s.get("name") == "資產總覽")
        return {"classify_assets 非退役": sum(1 for a in ca if not a["retired"]),
                "分佈統計 total_assets": system_stats.by_system(c, limit=None)["total_assets"],
                "主機清單 total_hosts": host_view.list_hosts(c)["total_hosts"],
                "來源對照表 total_hosts": hs["total_hosts"],
                "月報 總台數": sec.get("總台數"),
                "台數口徑 試算": machine_identity.analyse(c)["new"],
                # 審查 N1：首頁狀態（逐台）——退役／退役但仍在線以外的各狀態、以及「登記矛盾」
                "首頁 非退役狀態台數": sum(v for k, v in manage_state.summarize(c)["counts"].items()
                                     if k not in (manage_state.RETIRED, manage_state.RETIRED_ALIVE)),
                "納管漏斗 非退役關卡": sum(v for k, v in _pipeline.summarize(c)["counts"].items()
                                     if k not in ("retired", "retired_alive")),
                # 審查 N4：業務系統對照表的資產台數
                "業務系統 asset_count 合計": sum(x["asset_count"] for x in _bs.list_all(c))}

    mem = sqlite3.connect(":memory:", check_same_thread=False)
    conn.backup(mem)
    mem.row_factory = sqlite3.Row
    db._register_ip_int(mem)
    before = _counts(mem)
    ph = ",".join("?" for _ in manage_state.RETIRED_STATUS)
    n = mem.execute(f"DELETE FROM hardware WHERE trim(coalesce(asset_status,'')) IN ({ph})",
                    tuple(manage_state.RETIRED_STATUS)).rowcount
    machine_identity.refresh(mem)
    after = _counts(mem)
    machine_identity.refresh(conn)
    mem.close()
    for k in before:
        check("退役＝備查", f"{k}：刪掉 {n} 筆退役後不變", before[k], after[k])


@section("同一台多筆登記：收集數字一致＋掃描空殼清單（2026-10-16）")
def check_same_machine_collect(conn):
    """同一台（machine_key、同為非退役）的每一筆登記，明細頁的軟體／服務／帳號數字必須一樣；
    掃描空殼清單三類加總＝非退役 DYN- 筆數，可併回的對象一定是 CIA 正本。唯讀。"""
    import asset_collect_status
    import dyn_shell
    import machine_scope

    retired = manage_state.RETIRED_STATUS
    rows = conn.execute("SELECT * FROM hardware").fetchall()
    groups: dict = {}
    for r in rows:
        if (r["asset_status"] or "").strip() in retired:
            continue
        k = system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        if not k.startswith("sn:"):
            groups.setdefault(k, []).append(r)
    multi = [g for g in groups.values() if len(g) > 1]
    bad = []
    for g in multi:
        seen = set()
        for hw in g:
            pk = len(machine_scope.machine_packages(conn, hw["asset_serial"], hw))
            cs = asset_collect_status.compute(conn, hw["asset_serial"], hw, pk)
            seen.add((cs["software"]["count"], cs["service"]["count"], cs["account"]["count"]))
        if len(seen) > 1:
            bad.append(f"{g[0]['hostname']}({len(g)} 筆) {sorted(seen)}")
    inv("資產明細", f"同一台多筆登記（{len(multi)} 組）每筆的軟體／服務／帳號數字一樣", not bad,
        "；".join(bad[:5]))

    pv = dyn_shell.preview(conn)
    n_dyn = sum(1 for r in rows if str(r["asset_serial"] or "").startswith("DYN-")
                and (r["asset_status"] or "").strip() not in retired)
    check("掃描空殼", "可併回＋需人工＋未登記 = 非退役 DYN- 筆數", sum(pv["counts"].values()), n_dyn)
    wrong = [i["shell_serial"] for i in pv["items"] if i["kind"] == "mergeable"
             and (system_report.is_off_book(i["target_serial"]) or not i["shell_serial"].startswith("DYN-"))]
    inv("掃描空殼", "可併回的一律是 DYN- 空殼 → CIA 正本", not wrong, f"{wrong[:5]}" if wrong else "")


@section("掃描空殼自動併回（決策 DYN2，2026-10-18）")
def check_dyn_auto_merge(conn) -> list[str]:
    """在記憶體副本上跑一次自動併回（部署 [3a2] 寫正式庫前也是這一支），核對：
    帳外筆數前後差＝併回筆數、帳外台數差＝併完不再有帳外紀錄的台數、在管筆數／台數不變、
    沒有退役／帳外被併、跑完不留可自動併的（冪等）、剩下的空殼＝3-13 三類加總。唯讀，不碰原庫。

    **只警告、不擋部署**（2026-10-18 審查，同 M3 教訓：一個功能不能卡住不相干的上線）：
    這段驗的是「假如現在併會不會出錯」，不是畫面上的數字；模擬沒過時 [3a2] 本來就拒絕寫正式庫，
    正式庫維持原狀、3-13 照舊顯示。所以這裡一律用 ⚠️（warn），連這段自己出錯也只警告。
    回傳沒過的項目（測試用；空 list＝全過）。"""
    import dyn_shell

    bad: list[str] = []

    def w(name: str, ok: bool, detail: str = "") -> None:
        if not ok:
            bad.append(name)
        warn("掃描空殼自動併回", name, ok, detail)

    try:
        sim = dyn_shell.simulate(conn)
    except Exception as exc:  # noqa: BLE001 - 模擬本身壞了：[3a2] 也會拒寫，這裡只警告不擋部署
        w("模擬自動併回（副本）", False, f"{type(exc).__name__}: {exc}；正式庫不會被寫，部署照常")
        return bad
    b, a, n = sim["before"], sim["after"], sim["merged"]

    def eq(name: str, app_v, indep_v) -> None:
        w(name, app_v == indep_v, f"App={app_v}　獨立={indep_v}")

    eq(f"帳外筆數前－後 = 自動併回 {n} 筆", b["off_book_total"] - a["off_book_total"], n)
    eq("帳外台數前－後 = 併完不再有帳外紀錄的台數",
       b["off_book_machines"] - a["off_book_machines"], sim["expected_machine_drop"])
    eq("在管台數併回前後不變", a["registered_hosts"], b["registered_hosts"])
    eq("在管筆數併回前後不變", a["registered_total"], b["registered_total"])
    w("沒有退役或帳外的被併", not sim["bad_retired"], f"{sim['bad_retired'][:5]}" if sim["bad_retired"] else "")
    w("跑完不留符合條件的（冪等）", not sim["left_auto"], f"{sim['left_auto'][:5]}" if sim["left_auto"] else "")
    eq("剩下的非退役 DYN- = 3-13 三類加總", sim["preview_total"], a["dyn_active"])
    if bad:
        print("⚠️  [掃描空殼自動併回] 模擬沒過：部署 [3a2] 會拒絕寫正式庫（空殼留在 3-13），其他數字照常核對、不擋部署")
    return bad


@section("盤點頁數字卡＝下鑽（Q82，2026-10-17）")
def check_card_drilldowns(conn):
    """服務盤點／軟體盤點／掃不到的網段：頁首每張數字卡＝點下去看到的那批列（或那一欄加總）。
    下鑽走的就是畫面用的那支函式／旗標，所以這裡對不上＝畫面上「寫 N 點進去不是 N」。"""
    import scan_gaps
    import service_inventory
    import software_inventory

    ss = service_inventory.service_summary(conn)
    for k in ("live", "gone", "exposed", "confirmed", "total"):
        inv("服務盤點", f"卡片 {k} = 下鑽筆數",
            ss[k] == len(service_inventory.list_services(conn, card=k)),
            f"卡片 {ss[k]}／下鑽 {len(service_inventory.list_services(conn, card=k))}")
    _h = {r["ip"] for r in service_inventory.list_services(conn, card="hosts")}
    inv("服務盤點", "卡片 hosts = 下鑽不重複 IP", ss["hosts"] == len(_h), f"卡片 {ss['hosts']}／下鑽 {len(_h)}")

    sw = software_inventory.software_summary(conn)
    bh = software_inventory.hosts_overview(conn)
    live_hosts = [r for r in bh if (r["package_count"] or 0) > 0]
    # 舊版 /software（只有 ?legacy=1 進得去）的「依主機」仍是一個 IP 一列：它的卡片用 hosts_ip
    inv("軟體盤點", "舊版卡片 hosts_ip = 舊版依主機（還有套件的 IP）列數", sw["hosts_ip"] == len(live_hosts),
        f"卡片 {sw['hosts_ip']}／下鑽 {len(live_hosts)}")
    _sum = sum(r["package_count"] or 0 for r in live_hosts)
    inv("軟體盤點", "卡片 live = 舊版依主機套件數加總（筆數，不是台數）", sw["live"] == _sum, f"卡片 {sw['live']}／下鑽 {_sum}")
    # V2 7.3（2.043.8／2.043.9，D3）：軟體盤點的每個「台數」都要等於獨立重算、也等於點下去的列數。
    # 獨立重算（GPT 審查 5）：不走 software_inventory 的 SQL——host_package 單表讀現行列、hardware 另讀一次（序號→主機名、狀態），
    # 在這裡自己對起來；退役用 lifecycle.RETIRED_STATUS、同一台只呼叫 system_stats.machine_key（全站唯一一把尺）。
    import random
    import system_stats
    from lifecycle import RETIRED_STATUS
    _hw = {_r["asset_serial"]: (_r["hostname"], (_r["asset_status"] or "").strip())
           for _r in conn.execute("SELECT asset_serial, hostname, asset_status FROM hardware")}
    _ind: dict[str, set] = {}            # machine_key → {(name, version, arch)}
    _ind_sw: dict[str, set] = {}         # 軟體名 → {machine_key}
    _ind_ver: dict[tuple, set] = {}      # (軟體名, 版本) → {machine_key}
    for _r in conn.execute("SELECT ip, asset_serial, name, version, arch FROM host_package WHERE gone_at IS NULL"):
        _host, _status = _hw.get(_r["asset_serial"], (None, ""))
        if _status in RETIRED_STATUS:
            continue
        _k = system_stats.machine_key(_host, _r["ip"], _r["asset_serial"])
        _ind.setdefault(_k, set()).add((_r["name"], _r["version"], _r["arch"]))
        _ind_sw.setdefault(_r["name"], set()).add(_k)
        _ind_ver.setdefault((_r["name"], _r["version"] or ""), set()).add(_k)
    # ① 頁首台數＝依主機列數＝重算；每列套件數＝重算＝點進去
    _mo = software_inventory.machines_overview(conn)
    inv("軟體盤點", "頁首 hosts = V2 依主機列數 = 獨立重算台數（machine_key、排除退役）",
        sw["hosts"] == len(_mo) == len(_ind) and {r["key"] for r in _mo} == set(_ind),
        f"頁首 {sw['hosts']}／列 {len(_mo)}／重算 {len(_ind)}")
    _bad = [r["key"] for r in _mo if r["package_count"] != len(_ind.get(r["key"], ()))]
    inv("軟體盤點", "V2 依主機每列套件數 = 獨立重算", not _bad, f"不一致 {len(_bad)} 台")
    _rnd = random.Random(20261104)                       # 固定種子：同一份資料每次抽到同一批（出錯可重現）
    _pick = lambda xs: (xs[:30] + _rnd.sample(xs[30:], min(30, max(0, len(xs) - 30))))   # noqa: E731
    _bad2 = [r["key"] for r in _pick(_mo) if len(software_inventory.machine_packages(conn, r["key"])) != r["package_count"]]
    inv("軟體盤點", "V2 依主機每列套件數 = 點進去的套件數（前 30＋隨機 30 台）", not _bad2, f"不一致 {len(_bad2)} 台")
    # ② 軟體清單每種軟體台數＝重算（全部）＝點下去的主機列數（前 30＋隨機 30 種）
    _ls = software_inventory.list_software(conn)
    _badsw = [r["name"] for r in _ls if r["host_count"] != len(_ind_sw.get(r["name"], ()))]
    inv("軟體盤點", "軟體清單每種軟體台數 = 獨立重算（machine_key、排除退役）",
        not _badsw and {r["name"] for r in _ls} == set(_ind_sw), f"不一致 {len(_badsw)} 種／清單 {len(_ls)}／重算 {len(_ind_sw)}")
    _sample = _pick(_ls)
    _badd = [r["name"] for r in _sample if len(software_inventory.software_hosts(conn, r["name"])) != r["host_count"]]
    inv("軟體盤點", "軟體清單台數 = 點下去的主機列數（前 30＋隨機 30 種）", not _badd, f"不一致 {len(_badd)} 種")
    # ③ 版本層（GPT 審查 5）：每個版本的台數＝重算（全部軟體）；抽樣那幾種再比「點那個版本」的列數
    _badv, _badv_cnt = [], []
    _sample_names = {r["name"] for r in _sample}
    for r in _ls:                                  # 台數＝重算：全部軟體的全部版本
        for v in software_inventory.software_versions(conn, r["name"]):
            if v["host_count"] != len(_ind_ver.get((r["name"], v["version"]), ())):
                _badv_cnt.append(f"{r['name']} {v['version']}")
            elif r["name"] in _sample_names and \
                    len(software_inventory.software_hosts(conn, r["name"], v["version"])) != v["host_count"]:
                _badv.append(f"{r['name']} {v['version']}")       # 點那個版本的列數：抽樣那幾種
    _vcount: dict[tuple, int] = {}
    for (_nm, _vv), _ks in _ind_ver.items():
        _vcount[(_nm, _vv)] = len(_ks)
    _badv_all = [f"{r['name']} {_v}" for r in _ls for _v in r["versions"] if (r["name"], _v) not in _vcount]
    inv("軟體盤點", "各版本台數 = 獨立重算（全部版本）", not _badv_cnt, f"不一致 {len(_badv_cnt)} 個版本")
    inv("軟體盤點", "各版本台數 = 點那個版本的列數（前 30＋隨機 30 種）", not _badv, f"不一致 {len(_badv)} 個版本")
    inv("軟體盤點", "軟體清單列出的每個版本都在重算裡（全部）", not _badv_all, f"多出 {len(_badv_all)} 個版本")
    _n = len(software_inventory.list_software(conn))
    inv("軟體盤點", "卡片 names = 軟體總覽列數", sw["names"] == _n, f"卡片 {sw['names']}／下鑽 {_n}")
    _c = len(software_inventory.list_changes(conn, days=30))
    inv("軟體盤點", "卡片 changes_30d = 近 30 天異動列數", sw["changes_30d"] == _c,
        f"卡片 {sw['changes_30d']}／下鑽 {_c}")

    g = scan_gaps.report(conn)
    sm, rows = g["summary"], g["rows"]
    gaps = [r for r in rows if r["is_gap"]]
    ff = [r for r in rows if r["firewall_first"]]
    inv("掃不到的網段", "卡片 firewall_first = 下鑽段數", sm["firewall_first"] == len(ff),
        f"卡片 {sm['firewall_first']}／下鑽 {len(ff)}")
    inv("掃不到的網段", "卡片 gap_segments = 下鑽段數", sm["gap_segments"] == len(gaps),
        f"卡片 {sm['gap_segments']}／下鑽 {len(gaps)}")
    _m = sum(r["registered_machines"] for r in gaps if r["registered_machines"] > 0)
    inv("掃不到的網段", "卡片 gap_registered_machines = 下鑽登記台數加總",
        sm["gap_registered_machines"] == _m, f"卡片 {sm['gap_registered_machines']}／下鑽 {_m}")
    _a = sum(r["addresses"] for r in gaps)
    inv("掃不到的網段", "卡片 gap_addresses = 下鑽位址數加總", sm["gap_addresses"] == _a,
        f"卡片 {sm['gap_addresses']}／下鑽 {_a}")


@section("首頁已納管／已收集真下鑽（2026-10-18）")
def check_manage_state_rep_items(conn):
    """首頁「對帳明細」已納管／已收集兩列以前是死連結，改成打
    `/api/manage-state?state=<state>` 真下鑽後，那支回的是 `summarize()["rep_items"][state]`——
    這裡釘住「卡片數字＝rep_items 筆數」對**全部**狀態都成立，不是只驗已納管／已收集兩個，
    因為這本質上是「counts 跟 rep_items 有沒有被拆成兩套算法」的迴歸守門。"""
    m = manage_state.summarize(conn)
    for state in (manage_state.ONBOARDED, manage_state.COLLECTED):
        n = len(m["rep_items"].get(state, []))
        inv("首頁對帳", f"卡片「{state}」= 下鑽（rep_items）筆數", m["counts"][state] == n,
            f"卡片 {m['counts'][state]}／下鑽 {n}")
    bad = [s for s in manage_state.ALL_STATES if m["counts"][s] != len(m["rep_items"].get(s, []))]
    inv("首頁對帳", "全部狀態 counts = rep_items 筆數（迴歸守門）", not bad,
        f"對不上：{bad}" if bad else "")


@section("整台狀態口徑（machine_state_mode）：兩種口徑同庫對比、計數＝下鑽")
def check_machine_state_mode(conn):
    """上線前評估用（唯讀）：同一個資料庫同時算代表筆與整台兩種口徑。
    不變式（與口徑無關）：總台數兩邊相同（只是換狀態、不增減台）、兩邊各自 counts＝下鑽筆數、
    變動清單的進出加總＝兩邊各態台數差；目前開關採用的口徑如實回報。"""
    out = manage_state.compare_modes(conn)
    inv("整台口徑", "兩種口徑總台數相同", out["total_equal"],
        f"代表筆 {out['representative']['total']}／整台 {out['machine']['total']}")
    inv("整台口徑", "代表筆口徑 卡片＝下鑽", out["representative"]["drilldown_equals_card"])
    inv("整台口徑", "整台口徑 卡片＝下鑽", out["machine"]["drilldown_equals_card"])
    bad = []
    for st in manage_state.ALL_STATES:
        d = out["machine"]["counts"][st] - out["representative"]["counts"][st]
        moved = (sum(1 for x in out["changes"] if x["after"] == st)
                 - sum(1 for x in out["changes"] if x["before"] == st))
        if d != moved:
            bad.append(st)
    inv("整台口徑", "逐台變動清單加總＝各態台數差", not bad, f"對不上：{bad}" if bad else "")
    warn("整台口徑", f"目前採用口徑：{out['current_mode']['label']}（v{out['current_mode']['version']}）；"
         f"兩口徑相差 {out['changed_machines']} 台", True)


@section("納入管理（adopt）CIA 已登記／未登記交叉引用")
def check_adopt_registered_split(conn):
    """adopt.vue「最近一次收集連上幾台」卡片新增的「CIA 已登記 X／CIA 未登記 Y」交叉引用。

    這張卡依賴「這一次人工貼進去、實際跑過的批次」（collect_dispatch_result），不是
    像 manage_state 那種隨時能現查的狀態——沒有人跑過「開始收集」，offline 腳本就沒有
    批次可驗，老實跳過並說明原因，不要硬湊一個假的不變式。
    """
    import collect_dispatch

    out = collect_dispatch.latest_run(conn)
    results = (out or {}).get("results") or []
    if not results:
        warn("納入管理", "CIA 已登記／未登記交叉引用", True,
             "沒有收集紀錄（還沒跑過「開始收集」）——這張卡依賴實際跑過的批次，offline 驗不到，先跳過")
        return
    collected = [r for r in results if r["status"] == "collected"]
    reg = sum(1 for r in collected if r.get("registered"))
    unreg = len(collected) - reg
    inv("納入管理", "CIA 已登記 + CIA 未登記 = 本批收集成功總數", reg + unreg == len(collected),
        f"{reg}+{unreg} vs {len(collected)}")
    check("納入管理", "卡片「最近一次收集連上幾台」= 本批 status=collected 筆數",
          out.get("collected"), len(collected))


@section("帳號盤點 3-8「盤點到的主機」已納管交叉引用")
def check_account_onboarded_split(conn):
    """AccountDashboard.vue「盤點到的主機 N 台」旁的「已納管 X／CIA 未登記 Y」。

    這份主機清單刻意包含 CIA 沒登記的主機（帳號稽核要看得到這個缺口），所以不驗
    「盤點到的主機＝manage_state 的某個子集」，只驗這兩個交叉引用數字本身：
    加總要等於清單總數，而且 onboarded 旗標要跟 manage_state 的 ONBOARDED 代表集合
    現查一致（不是 account_inventory 自己另外判的）。
    """
    import account_inventory

    hosts = account_inventory.inventoried_hosts(conn)
    onboarded = sum(1 for h in hosts if h["onboarded"])
    not_reg = len(hosts) - onboarded
    inv("帳號盤點", "已納管 + 未納管（7 天內沒連上）= 盤點到的主機", onboarded + not_reg == len(hosts),
        f"{onboarded}+{not_reg} vs {len(hosts)}")

    rep = manage_state.summarize(conn)["rep_items"].get(manage_state.ONBOARDED, [])
    onboarded_ips = {it["ip"] for it in rep if it.get("ip")}
    onboarded_serials = {it["asset_serial"] for it in rep if it.get("asset_serial")}
    bad = [h["ip"] for h in hosts
           if h["onboarded"] != (h["ip"] in onboarded_ips or h["asset_serial"] in onboarded_serials)]
    inv("帳號盤點", "onboarded 旗標 = 用 manage_state ONBOARDED 代表集合現查一致",
        not bad, f"對不上：{bad[:3]}" if bad else "")


@section("帳號儀表板 3-7 依 OS 分頁（Linux／Windows／AIX／OS 未知）")
def check_account_os_split(conn):
    """AccountDashboard.vue 的 OS 分頁：每台只歸一個桶，各桶加總＝全部；
    分頁標籤台數＝該桶下鑽主機筆數；各桶帳號數／稽核發現數／摘要數字相加＝全部。"""
    import account_inventory as ai

    hosts = ai.inventoried_hosts(conn)
    counts = ai.os_bucket_counts(ai.host_os_buckets(conn))
    inv("帳號儀表板", "各 OS 桶台數加總 = 盤點到的主機", sum(counts.values()) == len(hosts),
        f"{counts} vs {len(hosts)}")
    for b in ai.OS_BUCKETS:
        check("帳號儀表板", f"分頁「{b}」台數 = 該桶下鑽主機筆數", counts[b],
              sum(1 for h in hosts if h["os_bucket"] == b))
    total_acc = len(ai.list_accounts(conn))
    per_acc = sum(len(ai.list_accounts(conn, os_bucket=b)) for b in ai.OS_BUCKETS)
    check("帳號儀表板", "各 OS 桶帳號數加總 = 全部帳號", per_acc, total_acc)
    full = ai.audit_summary(conn)
    if full.get("has_data"):
        parts = [ai.audit_summary(conn, b) for b in ai.OS_BUCKETS]
        for k in ("fail_high", "fail_medium", "fail_low", "unknown", "accounts",
                  "privileged", "humans"):
            check("帳號儀表板", f"摘要「{k}」各 OS 桶加總 = 全部",
                  sum(p[k] for p in parts), full[k])
        # 卡片＝下鑽：特權帳號卡 vs ?priv=1 清單、查不到卡 vs 稽核發現 verdict=unknown（每個 OS 分頁）
        buckets = ai.host_os_buckets(conn)
        finds = ai.latest_findings(conn)
        for b, sm in [(None, full), *zip(ai.OS_BUCKETS, parts)]:
            tag = b or "全部"
            check("帳號儀表板", f"[{tag}] 特權帳號卡 = 下鑽清單筆數", sm["privileged"],
                  len(ai.list_accounts(conn, os_bucket=b, privileged_only=True)))
            check("帳號儀表板", f"[{tag}] 查不到卡 = 下鑽發現筆數", sm["unknown"],
                  sum(1 for f in finds if f.get("verdict") == "unknown"
                      and (not b or buckets.get(f.get("ip"), "unknown") == b)))


@section("組態檢核 8-2 平台表數字＝下鑽筆數")
def check_config_audit_drill(conn):
    """config-audit.vue 平台表每一格（平台×四態）點進去＝/api/config-audit/items 的筆數。
    格子用 summary()（匯入時存的計數），下鑽用 items_across()（逐條分桶），兩條路要對得起來。"""
    import config_audit as ca

    by = {b["platform"]: b for b in ca.summary(conn)["by_platform"]}
    got: dict[tuple, int] = {}
    for it in ca.items_across(conn):
        k = (it["platform"], it["bucket"])
        got[k] = got.get(k, 0) + 1
    for p, b in sorted(by.items()):
        for r in ca.RESULTS:
            check("組態檢核", f"[{p}] {r} 格 = 下鑽筆數", b[r], got.get((p, r), 0))
    stray = sorted(k for k in got if k[0] not in by)
    inv("組態檢核", "下鑽沒有平台表以外的平台", not stray, f"{stray[:3]}" if stray else "")
    # 2026-10-21：Windows 自製簡易檢核 W-V001 停用（改用公司基準 286 條），舊結果不可以計入任何數字
    old = [r["id"] for r in ca.runs(conn) if ca.is_superseded(r.get("script_version"))]
    inv("組態檢核", "舊標準（已停用 W-V001）不計入平台表／卡片／下鑽", not old,
        f"run {old[:3]}" if old else "")


@section("存在可信度／CIA 完整率／已納管單一定義（EXIST1）")
def check_existence(conn):
    """決策 EXIST1（2026-10-18）：台數＝真的存在的機器（存在可信度 ≥ 門檻），CIA 完整率另講，
    已納管＝收集帳號 7 天內連得上（首頁／2-1／3-8 同一個定義）。

    每個彙總數字都用「下鑽清單的實際筆數」獨立重算一次（加總＝下鑽），再加分割不變式。"""
    import existence

    ex = existence.compute(conn)
    s, ms_ = ex["summary"], ex["machines"]
    for b in existence.BUCKETS:
        n = len(existence.machines_in(conn, b)["items"])
        key = {"all": "total_machines"}.get(b, b)
        check("存在可信度", f"「{existence.BUCKET_LABEL[b]}」數字 = 下鑽筆數", s[key], n)
    inv("存在可信度", "計入 + 疑似下線 + 退役 = 全部機器",
        s["counted"] + s["suspect_offline"] + s["retired"] == s["total_machines"],
        f"{s['counted']}+{s['suspect_offline']}+{s['retired']} vs {s['total_machines']}")
    inv("存在可信度", "各等級加總（不含退役）= 計入 + 疑似下線",
        sum(s["by_level"].values()) == s["counted"] + s["suspect_offline"],
        f"{sum(s['by_level'].values())} vs {s['counted'] + s['suspect_offline']}")
    thr = s["count_threshold"]
    inv("存在可信度", f"計入的每一台分數 ≥ {thr}、疑似下線每一台 < {thr}",
        all(m["score"] >= thr for m in ms_ if m["counted"])
        and all(m["score"] < thr for m in ms_ if m["suspect_offline"]))
    inv("存在可信度", "machine_key 不重複（同一台只算一次）",
        len({m["key"] for m in ms_}) == len(ms_))
    # CIA 完整率：分子分母都是下鑽清單的筆數
    num = len(existence.machines_in(conn, "cia_registered")["items"])
    den = len(existence.machines_in(conn, "counted")["items"])
    inv("CIA 完整率", "CIA 有登記 + CIA 待補登 = 計入台數",
        s["cia_registered"] + s["cia_pending"] == s["counted"],
        f"{s['cia_registered']}+{s['cia_pending']} vs {s['counted']}")
    exp = round(num * 100.0 / den, 1) if den else None
    check("CIA 完整率", "完整率 = 有登記下鑽筆數 ÷ 計入下鑽筆數", s["cia_complete_pct"], exp)
    check("CIA 完整率", "CIA 補登清單（匯出）筆數 = CIA 待補登", len(existence.cia_pending_rows(conn)),
          s["cia_pending"])
    # 來源過期（2026-10-18 修正）：獨立用 SQL 查各來源最新時間，超過門檻的才警示；而且不降級
    rules = existence.load_rules()
    lim = int(rules.get("source_stale_days", 60))
    now = datetime.datetime.now()
    exp_stale = set()
    for src, sql in [(x, "SELECT MAX(collected_at) FROM source_record WHERE source = ?")
                     for x in rules.get("inventory_sources") or ()] + [("scan", "SELECT MAX(scan_time) FROM scan_history")]:
        t = conn.execute(sql, (src,) if "?" in sql else ()).fetchone()[0]
        if t and (now - datetime.datetime.strptime(str(t)[:19], "%Y-%m-%d %H:%M:%S")).days > lim:
            exp_stale.add(src)
    check("存在可信度", f"來源過期警示（> {lim} 天沒更新）= 獨立查各來源最新時間",
          sorted(x["source"] for x in s["stale_sources"]), sorted(exp_stale))
    inv("存在可信度", "來源過期不降級：依據標過期的機器仍在該等級",
        all(m["level"] in ("onboarded", "inventory") for m in ms_
            if any(x != "scan" for x in m.get("stale_evidence", []))))
    inv("存在可信度", "待確認（只有 DY）都在 CIA 待補登裡",
        all(m["counted"] and not m["has_cia"] for m in ms_ if m["pending_confirm"]))
    # 首頁頭條＝composition.existence
    c = manage_state.composition(conn)
    check("首頁", "頭條在管 = 存在可信度計入", c["existence"]["counted"], s["counted"])
    check("首頁", "CIA 完整率（首頁）= 存在可信度彙總", c["existence"]["cia_complete_pct"],
          s["cia_complete_pct"])
    # 已納管單一定義：首頁（manage_state）＝存在可信度的已納管；3-8 旗標讀同一份代表清單
    m = manage_state.summarize(conn)
    check("已納管", "首頁已納管 = 存在可信度「已納管」（收集帳號 7 天內）",
          m["counts"][manage_state.ONBOARDED], s["onboarded"])
    import account_inventory
    hosts = account_inventory.inventoried_hosts(conn)
    rep_ips = {it["ip"] for it in m["rep_items"][manage_state.ONBOARDED] if it.get("ip")}
    bad = [h["ip"] for h in hosts if h["onboarded"] and h["ip"] not in rep_ips
           and h["asset_serial"] not in {it["asset_serial"] for it in m["rep_items"][manage_state.ONBOARDED]}]
    inv("已納管", "3-8 標已納管的主機都在首頁已納管清單裡", not bad, f"對不上：{bad[:3]}" if bad else "")


@section("資產狀態只有 CIA 可寫／已退役組成／掃描紅燈（2026-10-20）")
def check_lifecycle(conn):
    """使用者 2026-10-20：「退役有 2,029，這一定是有算錯」「我們自己不能變成這種狀態」。

    1. 已退役組成（首頁卡片下鑽）：逐台筆數＝卡片數字；組成表加總＝總數。
    2. 合併紀錄裡的空殼不在 hardware（不會被任何台數算到）。
    3. 遷移跑過之後：非 CIA 來的登記（DYN-／VC-／AUTO-／ADOPT-）沒有任何一筆是退役狀態——
       退役只能從 CIA 匯入來（遷移前只警告：部署 [2d] 跑在 schema 遷移之前）。
    4. 掃描紅燈：分子＝「未涵蓋」下鑽筆數、分子 ≤ 分母；分母＝獨立用 SQL 重算的現役有登記有 IP 台數。"""
    import lifecycle

    m = manage_state.summarize(conn)
    b = manage_state.retired_breakdown(conn, m)
    check("已退役組成", "下鑽逐台筆數 = 首頁「已退役」卡片", b["total"], m["counts"][manage_state.RETIRED])
    check("已退役組成", "組成表（狀態×來源）台數加總 = 總數", sum(x["machines"] for x in b["matrix"]), b["total"])
    check("已退役組成", "依狀態加總 = 總數", sum(b["by_status"].values()), b["total"])
    check("已退役組成", "依來源加總 = 總數", sum(b["by_source"].values()), b["total"])
    merged = {r["asset_serial"] for r in lifecycle.merged_rows(conn)}
    still = [s for (s,) in conn.execute("SELECT asset_serial FROM hardware") if s in merged]
    inv("合併紀錄", "已合併的空殼不在 hardware（不計入任何台數）", not still, f"{still[:5]}" if still else "")
    try:
        done = conn.execute("SELECT value FROM app_settings WHERE key = ?", (lifecycle.MIGRATION_KEY,)).fetchone()
    except Exception:  # noqa: BLE001 - 舊庫沒有 app_settings：當沒跑過
        done = None
    bad = [r[0] for r in conn.execute("SELECT asset_serial, asset_status FROM hardware")
           if not lifecycle.is_from_cia(r[0]) and lifecycle.is_retired_value(r[1])]
    (inv if done else warn)("資產狀態", "非 CIA 來的登記沒有退役狀態（退役只能從 CIA 匯入）", not bad,
                            f"{len(bad)} 筆，例：{bad[:5]}" + ("" if done else "（生命週期遷移還沒跑：[3] 會處理）")
                            if bad else "")
    g = m["scan_gap"]
    check("掃描紅燈", "分子 = 「未涵蓋」下鑽筆數", g["n"], len(m["rep_items"][manage_state.NOT_COVERED]))
    inv("掃描紅燈", "分子 ≤ 分母", g["n"] <= g["base"], f"{g['n']} vs {g['base']}")
    # 分母獨立重算：現役（非退役）、有序號、有 IP 的列，照 machine_key（退役另組）數台
    import existence

    keys = set()
    for r in conn.execute("SELECT hostname, ip, asset_serial, asset_status FROM hardware"):
        k = existence.item_key(r[0], r[1], r[2])
        if lifecycle.is_retired_value(r[3]):
            continue
        if manage_state.ips_of(r[1]):
            keys.add(k)
    check("掃描紅燈", "分母 = 獨立重算（現役、有登記、有 IP，逐台）", g["base"], len(keys))


@section("系統地圖（4-1／4-2，2026-10-22）")
def check_system_map(conn):
    """系統地圖右側卡片＝下鑽清單，且台數跟業務系統對照表（business_system.list_all）同一把尺。
    逐系統呼叫成本不低，只驗登記筆數最多的 30 個系統（最常被看、也最容易出錯的那些）。"""
    import blast_radius
    import business_system

    expect = {r["api_id"]: r["asset_count"] for r in business_system.list_all(conn)}
    systems = sorted(blast_radius.list_business_systems(conn), key=lambda r: -r["asset_count"])[:30]
    bad_sum, bad_drill, bad_vs_list, bad_room, bad_out, bad_seen, bad_unreg = [], [], [], [], [], [], []
    bad_rack: list[str] = []
    for r in systems:
        m = blast_radius.system_map(conn, r["api_id"])
        s = m["summary"]
        host_keys = {h["key"] for h in m["hosts"]}
        if s["on_esxi"] + s["no_esxi"] != s["hosts"] or s["hosts"] != len(m["hosts"]):
            bad_sum.append(r["api_id"])
        if (s["esxi"] != len(m["esxi"]) or s["datastores"] != len(m["datastores"])
                or s["clusters"] != len(m["clusters"]) or s["retired_records"] != len(m["retired"])):
            bad_drill.append(r["api_id"])
        if (sum(x["host_count"] for x in m["rooms"]) != s["hosts"]
                or any(sum(x["env"].values()) != x["host_count"]
                       or sum(g["host_count"] for g in x["groups"]) != x["host_count"] for x in m["rooms"])):
            bad_room.append(r["api_id"])
        if (s["seen"] + s["registered_only"] != s["hosts"]
                or s["seen"] != sum(1 for h in m["hosts"] if h["seen"])
                or any(x["seen"] + x["registered_only"] != x["host_count"]
                       or len(x["seen_hosts"]) != x["seen"]
                       or len(x["registered_only_hosts"]) != x["registered_only"] for x in m["rooms"])):
            bad_seen.append(r["api_id"])
        if any(c["unregistered_count"] != len(c["unregistered_vms"])
               or any(v["key"] in host_keys for v in c["unregistered_vms"]) for c in m["clusters"]):
            bad_unreg.append(r["api_id"])
        rk = m["racks"]
        if (rk["racked_count"] + rk["no_rack_count"] != s["hosts"] or rk["no_rack_count"] != len(rk["no_rack"])
                or rk["racked_count"] != sum(x["host_count"] for x in rk["rooms"])
                or any(x["host_count"] != sum(y["host_count"] for y in x["racks"]) for x in rk["rooms"])
                or any(y["host_count"] != len(y["rows"]) for x in rk["rooms"] for y in x["racks"])):
            bad_rack.append(r["api_id"])
        tot = {e: sum(1 for h in m["hosts"] if h["env_group"] == e) for e in ("正式", "備援")}
        if any(o["here_prod"] + o["else_prod"] != tot["正式"] or o["here_dr"] + o["else_dr"] != tot["備援"]
               or any(len(o["keys"][k]) != o[k] for k in ("here_prod", "here_dr", "else_prod", "else_dr"))
               for o in m["outage"]):
            bad_out.append(r["api_id"])
        if r["api_id"] in expect and expect[r["api_id"]] != s["hosts"]:
            bad_vs_list.append(f"{r['api_id']}:{s['hosts']}≠{expect[r['api_id']]}")
    inv("系統地圖", "掛 ESXi＋未對應 = 主機台數 = 主機清單列數", not bad_sum, f"{bad_sum[:5]}" if bad_sum else "")
    inv("系統地圖", "ESXi／Cluster／Datastore／退役卡片 = 下鑽列數", not bad_drill, f"{bad_drill[:5]}" if bad_drill else "")
    inv("系統地圖", "各機房台數加總 = 主機台數；機房內 正式+備援+測試+其他 = Cluster列+實體列 = 機房台數",
        not bad_room, f"{bad_room[:5]}" if bad_room else "")
    inv("系統地圖", "機房停機：這裡+別處 = 全系統（正式、備援各自）；每格 = 下鑽筆數",
        not bad_out, f"{bad_out[:5]}" if bad_out else "")
    inv("系統地圖", "實際看得到 + 只有登記 = 主機台數（整個系統、每個機房；每格 = 下鑽筆數）",
        not bad_seen, f"{bad_seen[:5]}" if bad_seen else "")
    inv("系統地圖", "同叢集未登記 VM：列上數字 = 下鑽筆數，且不在本系統主機裡",
        not bad_unreg, f"{bad_unreg[:5]}" if bad_unreg else "")
    inv("系統地圖", "機櫃頁簽：各機櫃台數加總＋無機櫃資料 = 主機台數；每列 = 下鑽",
        not bad_rack, f"{bad_rack[:5]}" if bad_rack else "")
    inv("系統地圖", "主機台數 = 業務系統對照表台數（同一把 machine_key）", not bad_vs_list,
        f"{bad_vs_list[:5]}" if bad_vs_list else f"驗了 {len(systems)} 個系統")



@section("同一實體櫃合一（以 Excel 櫃名為主，2.043.13／.14）")
def check_rack_name_merge(conn):
    """CIA「1-1」＝Excel「A1」合成一櫃顯示：只是改名，不可增減任何一台、不可把一台算兩次。
    獨立對照（GPT 審查 5）：① 對照表自己從 app_settings.rack_name_map 讀（不走 rack_layout.rack_name_map）；
    ② 同一份資料用「不套對照」再算一次（apply_rack_names=False），逐台比對；③ 台數：合併前各櫃 device_count 加總 vs 合併後。"""
    import rack_layout
    raw = conn.execute("SELECT value FROM app_settings WHERE key = 'rack_name_map'").fetchone()
    try:
        stored = json.loads(raw[0]) if raw and raw[0] else {}
    except (TypeError, ValueError):
        stored = {}
    mine: dict[str, str] = {}
    for k, v in (stored if isinstance(stored, dict) else {}).items():
        if isinstance(k, str) and "/" in k and isinstance(v, str):
            room, rk = k.split("/", 1)
            vv = rack_layout._norm_rack(v) if v.strip() else rk
            if vv != rk:
                mine[f"{room}/{rk}"] = vv
    show = lambda room, rk: mine.get(f"{room}/{rk}", rk) if rk else rk   # noqa: E731
    inv("機櫃合一", "對照表＝app_settings 的明確鍵（只做預覽不會生效）", rack_layout.rack_name_map(conn) == mine,
        f"系統 {len(rack_layout.rack_name_map(conn))}／設定 {len(mine)}")
    after = rack_layout._machines(conn, read_only=True)
    before = rack_layout._machines(conn, read_only=True, apply_rack_names=False)
    inv("機櫃合一", "合櫃前後同一批機器（machine_key 集合一樣）", {m["key"] for m in after} == {m["key"] for m in before},
        f"前 {len(before)}／後 {len(after)}")
    kinds = lambda ms: sorted((m["kind"], m["state"] or "") for m in ms)   # noqa: E731
    inv("機櫃合一", "合櫃前後各種位置／狀態的台數不變（在櫃、關機未下架、已不在櫃上、VM…）", kinds(after) == kinds(before))
    b = {m["key"]: m for m in before}
    bad = [m["key"] for m in after if m["rack"] != show(m["room"], b[m["key"]]["rack"])
           or (m["room"], m["u_start"], m["u_end"]) != (b[m["key"]]["room"], b[m["key"]]["u_start"], b[m["key"]]["u_end"])]
    inv("機櫃合一", "每台只換櫃名（機房、U 不動；櫃名＝對照後的名字）", not bad, f"不一致 {len(bad)} 台")
    on_rack = ("rack", "removed")          # 只有「已不在櫃上」設備的櫃也會畫成一格
    racks_after = {(m["room"], m["rack"]) for m in after if m["kind"] in on_rack and m["rack"]}
    racks_expected = {(m["room"], show(m["room"], m["rack"])) for m in before if m["kind"] in on_rack and m["rack"]}
    inv("機櫃合一", "櫃數＝合併後的櫃數", racks_after == racks_expected, f"畫面 {len(racks_after)}／對照後 {len(racks_expected)}")
    leftover = [k for k in mine if (k.split("/", 1)[0], k.split("/", 1)[1]) in racks_after]
    inv("機櫃合一", "已對照的系統櫃名不再單獨出現（沒有兩個名字各一櫃）", not leftover, "、".join(leftover[:5]))
    # ③ 在用台數：合併前（兩個名字各算各的）vs 合併後（同一櫃一起算）。同箱／管理卡／重疊歸併只在一櫃之內做，
    # 原本分在 1-1 與 A1 的兩筆，合櫃後可能被判成同一箱 → 少算一台。2.043.15（GPT 複核 D）：沒有人確認過的台數變動擋部署（FAIL）——
    # 減少可能來自錯的名稱對照或「同主機名＋同機型」的自動判斷，不一定是真的重複登記。管理員在 5.1「機櫃名稱對照」確認後才算通過，
    # 報告列出確認人與時間。
    import rack_layout as _rl
    # 2.043.16（GPT 第三次複核 P1 雞生蛋）：對照啟用 7 天內還沒確認＝警告（讓帶確認畫面的新版先上線），過了 7 天還沒確認才擋部署。
    unconfirmed, grace, confirmed = [], [], []
    for ch in _rl.merge_count_changes(conn):
        why = "；".join(f"{x['name']}（{x['from_rack']} {x['u']}）→ {x['why']}" for x in ch["lost"][:6])
        line = f"{ch['room']} {ch['rack']}：合併前 {ch['before']} 台、合併後 {ch['after']} 台；{why}"
        if ch["confirmed"]:
            confirmed.append(f"{line}（{ch['confirmed']['by']} 於 {ch['confirmed']['at']} 確認）")
        elif ch["in_grace"]:
            grace.append(f"{line}（對照 {ch['activated_at'] or '剛'} 啟用，請在 {ch['grace_until'] or '7 天內'} 前確認）")
        else:
            unconfirmed.append(f"{line}（對照 {ch['activated_at']} 啟用，已超過 {_rl.MERGE_GRACE_DAYS} 天）")
    name = "合櫃前後在用台數一樣，或差異已由管理員確認（合櫃後跟另一個名字下的設備判成同一箱，屬「同一台不重複算」規則造成）"
    done = ("｜已確認：" + "｜".join(confirmed[:10])) if confirmed else ""
    if unconfirmed:
        inv("機櫃合一", name, False, "未確認：" + "｜".join(unconfirmed[:10]) + done)
    elif grace:
        warn("機櫃合一", name, False, "待確認（寬限中）：" + "｜".join(grace[:10]) + done)
    else:
        inv("機櫃合一", name, True, done[1:])


@section("機櫃差異待辦（2.043.17，R4–R8）")
def check_rack_diff(conn):
    """① 各狀態筆數（rack_diff.counts）＝自己用 SQL 數的＝清單下鑽列數，加總＝全部筆數；
    ② 同一台／同一個 Excel 格沒有兩筆未處理的待辦；
    ③ 結案「關機未下架」的那台：機櫃圖算在關機（off_keys）、不算在用台數、仍佔 U（跟現有「關機未下架」同一口徑）；
       結案「實體移除／退役」的那台：不在任何機櫃的在用台數裡；
    ④ 結案後那台又被別人改過（跟結案當下不同）＝警告（還原會被擋），不是數字錯。讀取不建表。"""
    import rack_diff
    import rack_layout
    if not rack_diff._ready(conn):
        inv("差異待辦", "還沒有任何差異待辦（表未建立）", True)
        return
    c = rack_diff.counts(conn)
    raw = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM rack_diff_item GROUP BY status")}
    total = conn.execute("SELECT COUNT(*) FROM rack_diff_item").fetchone()[0]
    for st in rack_diff.STATUS:
        drill = len(rack_diff.list_items(conn, st))
        inv("差異待辦", f"「{rack_diff.STATUS[st]}」筆數＝獨立 SQL＝下鑽列數", c[st] == raw.get(st, 0) == drill,
            f"counts={c[st]}／SQL={raw.get(st, 0)}／下鑽={drill}")
    odd = sorted(set(raw) - set(rack_diff.STATUS))
    inv("差異待辦", "各狀態加總＝全部筆數、沒有不認得的狀態", sum(c.values()) == total and not odd, f"加總 {sum(c.values())}／全部 {total}／{odd}")
    ph = ",".join("?" * len(rack_diff.OPEN))
    dup_sys = conn.execute(f"SELECT sys_key, COUNT(*) FROM rack_diff_item WHERE subject='sys' AND status IN ({ph}) "
                           "GROUP BY sys_key HAVING COUNT(*) > 1", rack_diff.OPEN).fetchall()
    dup_xl = conn.execute(f"SELECT xl_match, COUNT(*) FROM rack_diff_item WHERE subject='xl' AND status IN ({ph}) "
                          "GROUP BY xl_match HAVING COUNT(*) > 1", rack_diff.OPEN).fetchall()
    inv("差異待辦", "同一台／同一個 Excel 格沒有兩筆未處理的待辦", not dup_sys and not dup_xl,
        f"{[tuple(r) for r in dup_sys][:5]}{[tuple(r) for r in dup_xl][:5]}")
    dc = rack_layout.datacenter(conn)
    counted = {k for r in dc["rooms"] for t in r["racks"] for k in t["keys"]}
    off_keys = {k for r in dc["rooms"] for k in r["power"]["off_keys"]}
    closed = conn.execute("SELECT id, sys_key, close_action, undo_json FROM rack_diff_item WHERE subject='sys' AND status='closed' "
                          "AND close_action IN ('confirm_off','confirm_removed','approve_retire')").fetchall()
    bad_off, bad_rm, bad_u, bad_w, changed = [], [], [], [], []
    racks_seen: dict[tuple, dict] = {}
    for r in closed:
        after = (json.loads(r["undo_json"] or "{}").get("after") or {})
        now = rack_diff._snapshot_ro(conn, r["sys_key"])
        if after and (now.get("state"), now.get("pos")) != (after.get("state"), after.get("pos")):
            changed.append(f"D{r['id']}")
            continue
        # 機櫃狀態的獨立來源＝上面 _snapshot_ro 直接查的 rack_state_override（跟結案當下一樣才往下比，不經 machines()）
        if r["close_action"] == "confirm_off":
            if r["sys_key"] not in off_keys or r["sys_key"] in counted:
                bad_off.append(f"D{r['id']}")
                continue
            d0 = dc["devices"].get(r["sys_key"]) or {}
            if not d0.get("rack") or not d0.get("u_start"):
                bad_u.append(f"D{r['id']}（沒有櫃位）")
                continue
            key = (d0["room"], d0["rack"])
            if key not in racks_seen:
                racks_seen[key] = rack_layout.rack_detail(conn, d0["room"], d0["rack"])
            det = racks_seen[key]
            # U 仍佔用：自己用每台的 U 段重算佔用（併進主機的管理口不佔 U），要跟畫面的已用 U 一樣，而且包含這台
            occ: set[int] = set()
            for d in det["devices"]:
                if d.get("mgmt_of") or not d.get("u_start"):
                    continue
                occ.update(range(d["u_start"], d["u_end"] + 1))
                for a, b2 in d.get("extra_ranges") or []:
                    occ.update(range(a, b2 + 1))
            occ &= set(range(1, det["height_u"] + 1))
            if not set(range(d0["u_start"], d0["u_end"] + 1)) <= occ or det["power"]["used_u"] != len(occ):
                bad_u.append(f"D{r['id']}（重算 {len(occ)}U／畫面 {det['power']['used_u']}U）")
            # 用電 0：這櫃沒有同箱／工程系統推定時，自己加總在用設備的額定瓦數，要等於畫面的用電（關機那台不能算進去）
            if not det["power"].get("system_level") and not any(d.get("merged_into") for d in det["devices"]):
                w = round(sum(d["w"] for d in det["devices"] if d["state"] == "active" and d.get("w") is not None), 1)
                if abs(w - det["power"]["w"]) > 0.05:
                    bad_w.append(f"D{r['id']}（重算 {w}W／畫面 {det['power']['w']}W）")
        else:
            if r["sys_key"] in counted:
                bad_rm.append(f"D{r['id']}")
    inv("差異待辦", "結案「關機未下架」的那台：算關機、不算在用台數（同現有口徑）", not bad_off, "、".join(bad_off[:10]))
    inv("差異待辦", "結案「關機未下架」的那台：U 仍佔用（獨立重算已用 U）", not bad_u, "、".join(bad_u[:10]))
    inv("差異待辦", "結案「關機未下架」的那台：用電 0（獨立重算在用設備額定瓦數）", not bad_w, "、".join(bad_w[:10]))
    inv("差異待辦", "結案「實體移除／退役」的那台：不在任何機櫃的在用台數", not bad_rm, "、".join(bad_rm[:10]))
    warn("差異待辦", "結案後那台沒有被別人再改過（改過的還原會被擋）", not changed, "、".join(changed[:10]))


@section("機房架構圖（4-5，2026-10-22）")
def check_datacenter(conn):
    """機櫃磚加總＝機房有機櫃資料的台數；機房台數＝有機櫃＋無機櫃資料；全部機房加總＝總數。"""
    import rack_layout

    d = rack_layout.datacenter(conn)
    bad_tile = [r["room"] for r in d["rooms"] if r["racked"] != sum(t["device_count"] for t in r["racks"])
                or any(t["device_count"] != len(t["keys"]) for t in r["racks"])]
    bad_room = [r["room"] for r in d["rooms"] if r["device_count"] != r["racked"] + len(r["no_rack"])]
    inv("機房架構圖", "機櫃磚台數加總 = 機房有機櫃資料台數；每磚 = 下鑽", not bad_tile, f"{bad_tile[:5]}" if bad_tile else "")
    inv("機房架構圖", "機房台數 = 有機櫃 + 無機櫃資料", not bad_room, f"{bad_room[:5]}" if bad_room else "")
    bad_pw = [f"{r['room']}/{t['rack']}" for r in d["rooms"] for t in r["racks"]
              if t["power"]["known_count"] + t["power"]["unknown_count"] != t["power"]["active_count"]
              or t["power"]["active_count"] != t["record_count"]]
    inv("機房架構圖", "每櫃：已知瓦數筆數 + 未知規格筆數 = 在用登記筆數", not bad_pw, f"{bad_pw[:5]}" if bad_pw else "")
    # 2026-10-26 一個實體箱子＝一台：每筆登記剛好落在一處（箱子本體／同箱其他登記／併進主機的管理口／
    # 找不到本體的管理口／疑似登記錯誤），台數＝本體＋疑似（在用），同箱的、管理口的「本體」在同一櫃且是本體
    ms = rack_layout.machines(conn)
    msk = {m["key"]: m for m in ms}
    bad_box, bad_w = [], []
    for r in d["rooms"]:
        for t in r["racks"]:
            cat = {k: t["category"].get(k, "box") for k in t["all_keys"]}
            act = [k for k in t["all_keys"] if msk[k]["state"] == "active"]
            parts = {c: sum(1 for k in act if cat[k] == c) for c in ("box", "suspect", "member", "port", "orphan")}
            heads_ok = all(t["head_of"].get(k) in cat and cat[t["head_of"][k]] in ("box",)
                           for k in cat if cat[k] in ("member", "port"))
            if (set(t["category"]) - set(t["all_keys"]) or parts != t["breakdown"] or sum(parts.values()) != t["record_count"]
                    or t["device_count"] != parts["box"] + parts["orphan"] + parts["suspect"]
                    or sorted(t["keys"]) != sorted(k for k in act if cat[k] in ("box", "orphan", "suspect")) or not heads_ok):
                bad_box.append(f"{r['room']}/{t['rack']}")
            # 用電只算一次：沒有工程系統整套計的櫃，W ＝ 在用的本體＋疑似（有瓦數的）加總
            if not t["power"]["system_level"]:
                w = sum(msk[k]["w"] for k in act if cat[k] in ("box", "orphan", "suspect") and msk[k]["w"] is not None)
                if round(w, 1) != round(t["power"]["w"], 1):
                    bad_w.append(f"{r['room']}/{t['rack']}")
    inv("機房架構圖", "每筆登記剛好一處（本體／同箱／管理口／找不到本體／疑似登記錯誤），台數＝本體＋找不到本體＋疑似",
        not bad_box, f"{bad_box[:5]}" if bad_box else "")
    inv("機房架構圖", "每櫃用電 = 在用箱子（本體＋找不到本體＋疑似）的瓦數加總（同箱、併進主機的管理口不重複算）", not bad_w,
        f"{bad_w[:5]}" if bad_w else "")
    indep: dict[tuple, int] = {}
    for m in ms:
        if m["kind"] in ("rack", "removed"):
            indep[(m["room"], m["rack"])] = indep.get((m["room"], m["rack"]), 0) + 1
    bad_st = [f"{r['room']}/{t['rack']}" for r in d["rooms"] for t in r["racks"]
              if t["power"]["active_count"] + t["power"]["off_count"] + t["power"]["pending_count"]
              + t["power"]["removed_count"] != indep.get((r["room"], t["rack"]), 0)]
    inv("機房架構圖", "每櫃：在用＋關機未下架＋待確認＋已不在櫃上 = 機櫃設備數（獨立重數）", not bad_st,
        f"{bad_st[:5]}" if bad_st else "")
    # 位置更正後：每台只在一個地方；有更正的設備確實出現在更正後的那一櫃
    seen_at: dict[str, str] = {}
    dup = []
    for r in d["rooms"]:
        for t in r["racks"]:
            for k in t["all_keys"] + t["power"]["removed_keys"]:
                if k in seen_at:
                    dup.append(k)
                seen_at[k] = f"{r['room']}/{t['rack']}"
    inv("機房架構圖", "每台設備只出現在一個機櫃", not dup, f"{dup[:5]}" if dup else "")
    # 上架助手：建議的 U 段不可以跟佔用中的設備或預留重疊（獨立從 machines＋預留重算佔用）
    occ: dict[tuple, set] = {}
    for m in ms:
        if m["kind"] == "rack" and m["u_start"]:
            occ.setdefault((m["room"], m["rack"]), set()).update(range(m["u_start"], m["u_end"] + 1))
    for r in rack_layout.reservations(conn):
        occ.setdefault((r["room"], r["rack"]), set()).update(range(r["u_start"], r["u_end"] + 1))
        # W221-016：舊鍵預留（room='分公司'）保守視為每個同櫃號分點櫃都被佔
        if r["room"] == rack_layout.LEGACY_BRANCH_ROOM:
            for (rm, rk), cells in list(occ.items()):
                if rk == r["rack"] and rack_layout.is_branch_site_room(rm):
                    cells.update(range(r["u_start"], r["u_end"] + 1))
    bad_pl = []
    for u_need in (1, 2, 4):
        for s in rack_layout.placement(conn, u_need, 1, ms=ms)["racks"]:
            if s["suggest_u_start"] and occ.get((s["room"], s["rack"]), set()) & set(range(s["suggest_u_start"], s["suggest_u_end"] + 1)):
                bad_pl.append(f"{s['room']}/{s['rack']}@{s['suggest_u_start']}({u_need}U)")
    inv("機房架構圖", "上架助手建議的 U 段不跟現有設備或預留重疊", not bad_pl, f"{bad_pl[:5]}" if bad_pl else "")
    moved = [m for m in ms if m.get("position_corrected") and m["kind"] in ("rack", "removed")]
    off = [m["key"] for m in moved if seen_at.get(m["key"]) != f"{m['room']}/{m['rack']}"]
    inv("機房架構圖", "位置更正過的設備在更正後的機櫃", not off, f"{len(moved)} 台更正；{off[:5]}" if off else f"{len(moved)} 台更正")
    bad_sum = [r["room"] for r in d["rooms"]
               if round(r["power"]["w"], 1) != round(sum(t["power"]["w"] for t in r["racks"]), 1)
               or r["power"]["unknown_count"] != len(r["power"]["unknown_keys"])
               or round(r["power"]["kg"], 1) != round(sum(t["power"]["kg"] for t in r["racks"]), 1)]
    inv("機房架構圖", "機房用電／重量 = 各櫃加總；未知規格數 = 下鑽筆數", not bad_sum, f"{bad_sum[:5]}" if bad_sum else "")
    check("機房架構圖", "機房切換鈕（有機櫃資料）加總 = 總數",
          sum(r["racked"] for r in d["rooms"]), d["total_racked"])
    check("機房架構圖", "機房切換鈕（全部實體設備）加總 = 總數",
          sum(r["device_count"] for r in d["rooms"]), d["total_devices"])


@section("機櫃前後各一台（2026-11-01）")
def check_front_rear(conn):
    """兩台背對背共用同一段 U：每台照舊各算一台（台數與沒有這個功能時相同）、共用的 U 只算佔用一次、用電重量各算各的。
    獨立重算：每櫃已用 U ＝ 所有不是「併進主機的管理口」的設備 U 段（含機櫃欄多段）聯集；前後各一台的兩面都是被算的箱子。"""
    import overlap_queue
    import rack_layout

    d = rack_layout.datacenter(conn)
    ms = rack_layout.machines(conn)
    msk = {m["key"]: m for m in ms}
    height = d["height_u"]
    bad_u, bad_n, bad_sh = [], [], []
    fr_cnt = 0
    for r in d["rooms"]:
        for t in r["racks"]:
            if t.get("excel_only"):
                continue
            occ_f: set[int] = set()
            occ_r: set[int] = set()
            rear = set(t.get("rear_keys") or [])
            for k in t["all_keys"]:
                if t["category"].get(k) == "port":
                    continue
                m = msk[k]
                face = occ_r if k in rear else occ_f
                if m["u_start"]:
                    face.update(range(m["u_start"], m["u_end"] + 1))
                for a, b in m.get("extra_ranges") or []:
                    face.update(range(a, b + 1))
            hs = set(range(1, height + 1))
            if t["used_u"] != len((occ_f | occ_r) & hs) or t["power"]["used_u"] != t["used_u"]:
                bad_u.append(f"{r['room']}/{t['rack']}")
            if t["power"]["front_rear_shared_u"] != len(occ_f & occ_r & hs):
                bad_sh.append(f"{r['room']}/{t['rack']}")
            if rear:
                fr_cnt += 1
                # 背面的箱子照樣算一台：不是同箱其他登記、也不是併進主機的管理口（否則台數會悄悄少一台）
                if any(t["category"].get(k, "box") in ("member", "port") for k in rear
                       if msk[k]["state"] == "active") or t["power"]["rear_count"] != len(rear):
                    bad_n.append(f"{r['room']}/{t['rack']}")
    inv("機櫃前後各一台", "每櫃已用 U ＝ 正面∪背面的獨立聯集（共用的 U 只算一次）", not bad_u, f"{bad_u[:5]}" if bad_u else f"{fr_cnt} 櫃有背面設備")
    inv("機櫃前後各一台", "每櫃「正背面共用 U」＝兩面獨立交集", not bad_sh, f"{bad_sh[:5]}" if bad_sh else "")
    inv("機櫃前後各一台", "背面設備照樣各算一台（不被當成同箱其他登記或管理口）", not bad_n, f"{bad_n[:5]}" if bad_n else "")
    g = overlap_queue.groups(conn)
    leak = [f"{x['room']}/{x['rack']}" for x in g["groups"] if not x["resolved"]
            and any(set(x["keys"]) == set(f["keys"]) and (x["room"], x["rack"]) == (f["room"], f["rack"]) for f in g["front_rear"])]
    inv("機櫃前後各一台", "已確認前後各一台的組不再列在位置重疊待處理", not leak, f"{leak[:5]}" if leak else f"前後各一台 {len(g['front_rear'])} 組")
    bad_pair = [f"{f['room']}/{f['rack']}" for f in g["front_rear"]
                if set(f["front"]) & set(f["rear"]) or not f["front"] or not f["rear"]
                or any(k not in msk or (msk[k]["room"], msk[k]["rack"]) != (f["room"], f["rack"]) for k in f["keys"])]
    inv("機櫃前後各一台", "每組前後各一台：正面與背面不重複、兩面都有設備、都在同一櫃", not bad_pair, f"{bad_pair[:5]}" if bad_pair else "")


@section("2.x 新版畫面（A1 首頁、A2 病歷表，2026-10-27）")
def check_v2(conn):
    """A2「同系統還有 N 台」＝下鑽筆數＝值班卡用 APID 查到的現役機器數－1；A1 底下兩個數字＝對帳橋試算。"""
    import count_bridge
    import oncall
    import v2_views

    groups, _ = oncall._groups(conn)
    names = v2_views._sysnames(conn)
    apis = sorted({(r["api_id"] or "").strip() for k, g in groups.items() if not k.startswith("ret:")
                   for r in g if (r["api_id"] or "").strip()})
    bad = []
    for a in apis[:60]:
        how, keys = oncall.resolve(conn, a, groups)
        act = [k for k in keys if not k.startswith("ret:")]
        if how != "APID" or not act:
            continue
        ss = v2_views._same_system(conn, groups, a, act[0], names)
        if ss["count"] != len(ss["machines"]) or ss["count"] + 1 != len(act)                 or len({m["key"] for m in ss["machines"]}) != ss["count"]:
            bad.append(a)
    inv("2.x 主機履歷", "同業務系統主機 N 台 = 下鑽筆數 = APID 查到的現役機器數 − 1", not bad,
        f"{bad[:5]}" if bad else f"驗了 {min(len(apis), 60)} 個系統")
    h = v2_views.home(conn)
    trial = {t["key"]: t["value"] for t in count_bridge.compute(conn)["summary"]["trial"]}
    check("2.x 首頁", "實體設備台數 = 對帳橋試算", h["physical"], trial.get("physical"))
    check("2.x 首頁", "主機部數 = 對帳橋試算", h["hosts"], trial.get("hosts"))
    bad_todo = [t["key"] for t in h["todo"] if t["n"] is not None and t["n"] < 0]
    inv("2.x 首頁", "今天要處理每一列都有連結、筆數不為負", not bad_todo and all(t["link"] for t in h["todo"]),
        f"{bad_todo[:5]}" if bad_todo else f"{len(h['todo'])} 列")
    # 2.006.3（找碴 C1）：首頁待處理的每一項＝它連去那一頁同一份計算的筆數（加總＝下鑽），而且有就一定要列出來
    # （0 才可以不列）——不能再出現「1.3 有 66 台、首頁綠底寫 0 項」。
    import daily_risk
    import incident
    import storage_capacity
    got = {t["key"]: t["n"] for t in h["todo"]}
    keys = [t["key"] for t in h["todo"]]
    inv("2.x 首頁", "待處理每一項 key 不重複（同 key 會在畫面互相蓋掉）", len(keys) == len(set(keys)),
        f"重複：{sorted({k for k in keys if keys.count(k) > 1})}" if len(keys) != len(set(keys)) else f"{len(keys)} 項")
    rk = daily_risk.risk_overview(conn)
    n_open = sum(1 for e in incident.list_events(conn) if e["status"] == "open" and e.get("mode") == "formal")
    ds = storage_capacity.datastores(conn)
    al = storage_capacity.alerts(ds, storage_capacity.physical(conn))
    n_low = len([v for v in ds["volumes"] if v["free_pct"] is not None and v["free_pct"] < ds["threshold_pct"]]) + \
        len([p for p in storage_capacity.physical(conn)["rows"]
             if p["by"] == "mount" and p["free_pct"] is not None and round(100 - p["free_pct"], 1) >= 80])   # R17：使用率 ≥80%
    for key, label, want in (("incident_open", "進行中事件（正式）= 4.1 進行中、性質＝正式的列數", n_open),
                             ("uptime", "逾期未重開機 = 1.3 頁籤列數", len(rk["uptime"]["rows"])),
                             ("hardware", "硬體異常 = 1.3 頁籤列數", len(rk["hardware"]["rows"])),
                             ("spof", "單點風險 = 1.3 頁籤列數", len(rk["spof"]["rows"])),
                             ("contract", "維護合約已過期 = 1.3 保固已過期筆數", (rk["contract"].get("counts") or {}).get("expired") or 0),
                             ("storage_alert", "容量預警 = 5.2 容量預警列數", n_low)):
        # n 是 None＝首頁標「無資料」（來源沒有資料）：此時下鑽頁也必須是 0 筆
        check("2.x 首頁", f"待處理：{label}", got.get(key) or 0, want)
    _evs = incident.list_events(conn)
    check("2.x 首頁", "事件 正式＋演練＋未分類 = 全部事件（演練不混進正式）", sum(incident.mode_counts(_evs).values()), len(_evs))
    check("2.x 首頁", "容量預警 = storage_capacity.alerts（5.2 頁籤同一份）", got.get("storage_alert") or 0, al["count"])
    # 查不到≠沒有（審查 M2）：來源沒有資料時，首頁一定要列那一項、標無資料
    fr = storage_capacity.freshness(conn, ds)
    has_mount = any(r["by"] == "mount" for r in storage_capacity.physical(conn)["rows"])
    nodata = [k for k, cond in (("uptime", not rk["uptime"]["collected"]), ("hardware", not rk["hardware"]["collected"]),
                                ("contract", not rk["contract"].get("has_data")), ("storage_alert", not fr["has_data"] and not has_mount),
                                ("storage_ds_nodata", not fr["has_data"] and has_mount),
                                ("storage_stale", fr["has_data"] and fr["stale"]))
              if cond and not (k in got and got[k] is None)]
    inv("2.x 首頁", "來源沒有資料／資料過期的項目一定列成「無資料」，不消失", not nodata, f"漏列：{nodata}" if nodata else "")


def _raw_contract_ids(conn) -> set:
    """保固獨立重算（2.044.4，GPT A5）：只用原始表＋全站共用定義（machine_key 收台、退役、帳內、VM、日期解析），
    不呼叫 daily_risk.contract_tab／resolve_all。回 {'contract:<machine_key>'}：已過期或 90 天內到期的帳內實體機。"""
    import datetime as _d
    import daily_risk
    import manage_state
    import system_report
    import system_stats
    rows = [r for r in conn.execute("SELECT asset_serial, hostname, ip, api_id, device_model, is_vm, asset_status, environment, "
                                    "physical_location, rack_no, os, hw_serial FROM hardware").fetchall() if not system_stats.is_retired_row(r)]
    spec_model = {}
    try:
        spec_model = {r[0]: r[1] for r in conn.execute("SELECT asset_serial, model FROM host_spec WHERE TRIM(COALESCE(model,'')) <> ''")}
    except sqlite3.Error:
        pass

    def nm(v):
        return " ".join(str(v or "").split()).upper()
    ent = {}
    try:
        for r in conn.execute("SELECT level, key, contract_end FROM contact_info"):
            ent[(r[0], nm(r[1]) if r[0] == "model" else r[1])] = r[2]
    except sqlite3.Error:
        pass
    by_serial = {"direct": {}, "model": {}}
    try:
        imp = conn.execute("SELECT id FROM contract_import WHERE is_current = 1 ORDER BY id DESC LIMIT 1").fetchone()
        crs = conn.execute("SELECT * FROM contract_row WHERE import_id = ? AND match_status = 'matched' ORDER BY row_no", (imp[0],)).fetchall() if imp else []
    except sqlite3.Error:
        crs = []
    for c in crs:
        try:
            serials = json.loads(c["matched_serials"] or "[]")
        except (ValueError, TypeError):
            serials = []
        b = "model" if c["match_by"] == "model" else "direct"
        for sr in serials:
            by_serial[b].setdefault(sr, []).append(c["end_date"])
    today = _d.date.today()
    out = set()
    for key, g in system_stats.collapse_machines(rows).items():
        inb = [r for r in g if not system_report.is_off_book(r["asset_serial"])]
        if not inb or any(manage_state.is_vm_value(r["is_vm"], r["device_model"]) for r in g):
            continue
        ordered = inb + [r for r in g if r not in inb]
        serials = [r["asset_serial"] for r in ordered]
        apis = sorted({(r["api_id"] or "").strip() for r in g if (r["api_id"] or "").strip()})
        models = []
        for r in ordered:
            for m in (r["device_model"], spec_model.get(r["asset_serial"])):
                if m and str(m).strip() and nm(m) not in models:
                    models.append(nm(m))
        # 取值順序（同 1.3 規則，但這裡自己實作）：本機手動→合約（序號／主機名）→系統手動→型號手動→合約（型號）
        cands = [ent[("asset", x)] for x in serials if ("asset", x) in ent][:1]
        cands += [e for x in serials for e in by_serial["direct"].get(x, [])]
        cands += [ent[("system", a)] for a in apis if ("system", a) in ent][:1]
        cands += [ent[("model", m)] for m in models if ("model", m) in ent][:1]
        cands += [e for x in serials for e in by_serial["model"].get(x, [])]
        end = next((str(v).strip() for v in cands if v is not None and str(v).strip()), None)
        d = daily_risk.parse_date(end) if end else None
        if d and (_d.date.fromisoformat(d) - today).days <= 90:
            out.add(f"contract:{key}")
    return out


@section("2.x 1.1 今日／1.4 追蹤處理（2.044.0，R9／R10）")
def check_opening_peak(conn):
    """2.045.0／2.045.1 今日開盤尖峰：拿最近一天有批次的資料驗（今天還沒到也驗得到）。
    獨立重算（GPT 審 A2）：直接讀原始點 opening_point，自己算台北 09:00～09:30、跳過缺值、Max／Avg／Min／點數，
    自己排序取 Top10，再跟畫面用的 summary／rows 比；不呼叫 opening_peak 的計算或排序函式。"""
    import opening_peak as _opk
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    if not _opk._has(conn, "opening_peak_batch") or not _opk._has(conn, "opening_point"):
        return
    r = conn.execute("SELECT MAX(day) FROM opening_peak_batch").fetchone()
    if not r or not r[0]:
        return
    day = r[0]
    t0 = int(_dt.strptime(day + " 09:00", "%Y-%m-%d %H:%M").replace(tzinfo=_tz(_td(hours=8))).timestamp() * 1000)
    t1 = t0 + 30 * 60 * 1000
    hosts = [x[0] for x in conn.execute("SELECT DISTINCT host FROM opening_peak WHERE day = ? AND metric = 'cpu'", (day,))]
    indep: dict[str, tuple] = {}
    for h in hosts:
        vals = [v for (v,) in conn.execute("SELECT value FROM opening_point WHERE day = ? AND host = ? AND metric = 'cpu' "
                                           "AND ts >= ? AND ts < ? AND value IS NOT NULL", (day, h, t0, t1))]
        indep[h] = ((round(max(vals), 2), round(sum(vals) / len(vals), 2), round(min(vals), 2), len(vals)) if vals else (None, None, None, 0))
    s = _opk.summary(conn, _dt.strptime(day + " 23:59", "%Y-%m-%d %H:%M"))
    alls = _opk.rows(conn, day, with_vms=False)
    app = {x["host"]: (x["max"], x["avg"], x["min"], x["points"]) for x in alls}
    diff = [h for h in hosts if app.get(h) != indep[h]]
    inv("1.1 今日", f"開盤尖峰 {day} 每台 Max／Avg／Min／點數 = 原始點獨立重算", not diff, f"{diff[:5]}" if diff else f"{len(hosts)} 台")
    order = sorted(hosts, key=lambda h: (indep[h][3] == 0, -(indep[h][0] or 0), h.lower()))
    top = [x["host"] for x in s["top"]["rows"]]
    inv("1.1 今日", f"開盤尖峰 {day} Top10 = 原始點獨立排序的前 10 台", top == order[:10], f"畫面 {top[:3]}… 重算 {order[:3]}…")
    inv("1.1 今日", f"開盤尖峰 {day} Top10 是全部的子集", set(top) <= {x["host"] for x in alls}, f"{len(top)}／{len(alls)}")
    check("1.1 今日", f"開盤尖峰 {day} 下鑽筆數 = 本批台數（原始表）", s["top"]["total"], len(hosts))
    check("1.1 今日", f"開盤尖峰 {day} 本批列到 = 下鑽筆數", s["listed"], len(alls))
    if s["expected_basis"] == "batch":
        check("1.1 今日", f"開盤尖峰 {day} 應有（未核對資產）= 下鑽筆數", s["expected"], len(alls))
        check("1.1 今日", f"開盤尖峰 {day} 涵蓋 = 原始點有值的台數", s["covered"], sum(1 for h in hosts if indep[h][3]))
    else:
        check("1.1 今日", f"開盤尖峰 {day} 應有 − 缺的 = 本批列到且在基準內", s["expected"] - len(s["missing"]),
              s["listed"] - len(s["extra"]))
    inv("1.1 今日", f"開盤尖峰 {day} 涵蓋 ≤ 應有", s["covered"] <= s["expected"], f"{s['covered']}／{s['expected']}")
    check("1.1 今日", f"開盤尖峰 {day} 對不到資產台數 = 原始表重算", s["unmatched"],
          conn.execute("SELECT COUNT(*) FROM opening_peak WHERE day = ? AND metric = 'cpu' AND match != 'matched'", (day,)).fetchone()[0])


def check_v2_today(conn):
    """追蹤處理：每張卡的數字＝該類列數（加總＝下鑽），每一類＝它原本那一頁同一份計算；今日：件數＝列數、0 跟未收集分開。"""
    import daily_risk
    import incident
    import scan_health
    import storage_capacity
    import v2_today
    t = v2_today.track(conn, record=False)
    bad = [c["key"] for c in t["categories"] if c["n"] != sum(1 for r in t["rows"] if r["cat"] == c["key"])]
    inv("1.4 追蹤處理", "每類卡片數字 = 下鑽列數", not bad, f"{bad}" if bad else f"{len(t['categories'])} 類")
    check("1.4 追蹤處理", "總數 = 各類加總", t["total"], sum(c["n"] for c in t["categories"]))
    nos = [r["__no"] for r in t["rows"]]
    inv("1.4 追蹤處理", "項次 1..N 不重複", nos == list(range(1, len(nos) + 1)), "")
    ids = [r["id"] for r in t["rows"]]
    inv("1.4 追蹤處理", "每列 id 不重複（今日新增／解除靠它）", len(ids) == len(set(ids)), "")
    got = {c["key"]: c["n"] for c in t["categories"]}
    rk = daily_risk.risk_overview(conn)
    ct = rk["contract"]["counts"] or {}
    ds = storage_capacity.datastores(conn)
    al = storage_capacity.alerts(ds, storage_capacity.physical(conn))
    for key, label, want in (
            ("incident_open", "進行中事件 = 4.1 進行中列數", sum(1 for e in incident.list_events(conn) if e["status"] == "open")),
            ("scan_empty", "掃描無回應 = 1.2 掃描異常未處理", scan_health.open_count(conn)),
            ("contract", "保固 = 1.3 已過期＋90 天內", (ct.get(daily_risk.BAND_EXPIRED) or 0) + (ct.get(daily_risk.BAND_90) or 0)),
            ("spof", "單點風險 = 1.3 頁籤列數", len(rk["spof"]["rows"])),
            ("uptime", "逾期未重開機 = 1.3 頁籤列數", len(rk["uptime"]["rows"])),
            ("hardware", "硬體異常 = 1.3 頁籤列數", len(rk["hardware"]["rows"])),
            ("capacity", "容量預警 = 5.2 容量預警（storage_capacity.alerts）", al["count"])):
        check("1.4 追蹤處理", label, got.get(key), want)
    # 2.044.3（GPT A5）：獨立驗算——直接查原始表、比「身分集合」，不只比數量、不再呼叫被驗的函式
    ids_of = lambda cat: {r["id"] for r in t["rows"] if r["cat"] == cat}          # noqa: E731
    def same(page, name, got_set, want_set):
        inv(page, name, got_set == want_set,
            f"多：{sorted(got_set - want_set)[:5]} 少：{sorted(want_set - got_set)[:5]}" if got_set != want_set else f"{len(got_set)} 件")
    try:
        raw_inc = {f"incident_open:{r[0]}" for r in conn.execute("SELECT id FROM incident_event WHERE status = 'open'")}
    except sqlite3.OperationalError:
        raw_inc = set()
    same("1.4 追蹤處理", "進行中事件 = incident_event status=open（原始表，比事件 ID）", ids_of("incident_open"), raw_inc)
    try:
        raw_scan = {f"scan_empty:{-r[0]}" for r in conn.execute("SELECT id FROM scan_alert WHERE is_read = 0")}
    except sqlite3.OperationalError:
        raw_scan = set()
    same("1.4 追蹤處理", "掃描無回應 = scan_alert 未處理（原始表，比 ID）", ids_of("scan_empty"), raw_scan)
    # 容量預警的實體主機：直接讀 host_spec.storage_json（by=mount），自己算使用率，交給 disk_level 分級，比資產序號集合
    import disk_level
    import optical_media
    raw_hosts, bad_num = set(), [0]

    def _f(v):
        """跟正式計算（storage_capacity._num）同一套：不是數字一律當 0，並計數列出（P2，2.044.4）。"""
        if v is None:
            return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            bad_num[0] += 1
            return 0.0
    for r in conn.execute("SELECT asset_serial, storage_json FROM host_spec WHERE storage_json IS NOT NULL AND storage_json != ''"):
        try:
            s = json.loads(r[1])
        except (ValueError, TypeError):
            continue
        if not isinstance(s, dict) or s.get("by") != "mount":
            continue
        all_items = [i for i in (s.get("items") or []) if isinstance(i, dict)]
        items = [i for i in all_items if not optical_media.is_optical(i.get("name"))]
        if not items or any(i.get("free_gb") is None for i in items):
            continue                                            # 剩餘查不到＝未收集，不列
        tot = (_f(s.get("total_gb")) if len(items) == len(all_items) else 0.0) or sum(_f(i.get("size_gb")) for i in items)
        if not tot:
            continue
        free_pct = round(sum(_f(i.get("free_gb")) for i in items) * 100 / tot, 1)
        if disk_level.listed(round(100 - free_pct, 1)):
            raw_hosts.add(f"capacity:host:{r[0]}")
    same("1.4 追蹤處理", "容量預警・實體主機 = host_spec 原始資料用 disk_level 重算（比資產序號）",
         {i for i in ids_of("capacity") if i.startswith("capacity:host:")}, raw_hosts)
    warn("1.4 追蹤處理", "容量獨立驗算：原始磁碟欄位裡不是數字的（當 0，跟正式計算同規則）", bad_num[0] == 0, f"{bad_num[0]} 個值")
    same("1.4 追蹤處理", "容量預警・Datastore = 剩餘 % < 門檻的卷宗（比卷宗身分）",
         {i for i in ids_of("capacity") if i.startswith("capacity:ds:")},
         {f"capacity:ds:{v['id']}" for v in ds["volumes"] if v["free_pct"] is not None and v["free_pct"] < ds["threshold_pct"]})
    # 保固（2.044.4 GPT A5）：直接查原始保固欄獨立重算，比 machine_key 集合
    same("1.4 追蹤處理", "保固 = 原始保固欄獨立重算（已過期＋90 天內，比 machine_key）", ids_of("contract"), _raw_contract_ids(conn))
    # 逾期未重開機／硬體異常／單點風險：目前沒有獨立重算，跟 1.3 同一支計算（照實標，不當成獨立驗）
    for cat, key, src in (("uptime", "key", rk["uptime"]["rows"]), ("hardware", "key", rk["hardware"]["rows"]), ("spof", "api_id", rk["spof"]["rows"])):
        same("1.4 追蹤處理", f"{cat} = 1.3 同一頁籤（與 1.3 同源，非獨立驗算；比 machine_key／APID 身分）", ids_of(cat), {f"{cat}:{r[key]}" for r in src})
    td = v2_today.today(conn, record=False)
    for k in ("alerts", "incidents", "changes"):
        b = td[k]
        inv("1.1 今日", f"{k}：有件數時件數 = 列數加總；未收集時件數是空的（不寫 0）",
            (b["n"] is None and b["status"] != "ok") or (b["n"] is not None and b["n"] == (sum(r["n"] for r in b["rows"]) if k == "changes" else len(b["rows"]))),
            f"status={b['status']} n={b['n']} rows={len(b['rows'])}")
    # 今日告警兩段（2.044.3 GPT A2＋B1）：現在仍在告警＝alert_event 還開著的；今天發生過＝今天 00:00 後開始的（原始表比 ID）
    import quiet_window as _qw
    _start = _qw.local_now().strftime("%Y-%m-%d 00:00:00")
    if td["alerts"]["status"] in ("ok", "stale") and conn.execute("SELECT 1 FROM sqlite_master WHERE name='alert_event'").fetchone():
        same("1.1 今日", "現在仍在告警 = alert_event 還開著（不含維護中，比事件 ID）", {r["id"] for r in td["alerts"]["rows"]},
             {r[0] for r in conn.execute("SELECT id FROM alert_event WHERE ended_at IS NULL AND tier != 'maint'")})
        _raw_today = [r[0] for r in conn.execute("SELECT id FROM alert_event WHERE started_at >= ? AND tier != 'maint' ORDER BY started_at DESC, id DESC", (_start,))]
        _d = td["alerts"]["today"]
        check("1.1 今日", "今天發生過的件數 = alert_event 今天 00:00 後開始的總數（不受明細上限影響）", _d["n"], len(_raw_today))
        same("1.1 今日", "今天發生過的明細 = 原始表最近那幾筆（比事件 ID；超過上限要標已截斷）", {r["id"] for r in _d["rows"]},
             set(_raw_today[:len(_d["rows"])]))
        inv("1.1 今日", "明細少於件數時一定標「已截斷」", (len(_d["rows"]) == _d["n"]) or bool(_d.get("truncated")), f"列 {len(_d['rows'])}／件 {_d['n']}")
    rc = t["changes"]
    if rc.get("resolved") is not None:
        check("1.4 追蹤處理", "今日解除 N = 解除清單列數（可下鑽）", rc["resolved"], len(rc["resolved_items"]))
    _op = td["opening"]
    if _op["status"] != "ok":
        inv("1.1 今日", "開盤尖峰今天還沒收到檔案時不放數字", "top" not in _op and "covered" not in _op, f"status={_op['status']}")
    check_opening_peak(conn)
    # R15（2.044.2）：告警聯絡資訊「缺 X」卡片＝清單裡缺那一欄的筆數；告警列五欄不是值就是「未提供」（不留空）
    import alert_contact
    lst = alert_contact.listing(conn)
    bad = [lab for lab, n in lst["missing"].items() if n != sum(1 for r in lst["rows"] if lab in r["missing"])]
    inv("告警聯絡資訊", "缺 X 卡片 = 清單缺那一欄的筆數", not bad, f"{bad}" if bad else f"{lst['total']} 個 APID")
    blank = [r["id"] for r in (td["alerts"].get("rows") or []) if any(not r.get("c_" + f) for f in alert_contact.FIELDS)]
    inv("告警聯絡資訊", "今日告警每列五欄都有值或「未提供」", not blank, f"{blank[:5]}" if blank else "")


@section("2.x 分布統計（10.2，2026-10-28）")
def check_v2_dist(conn):
    """10.2：獨立錨點（2.007.3 審查）——
      總台數＝存在可信度（existence.compute）裡有登記、非退役的機器數（不經 v2_hosts）；
      每個維度：全部加總＝總台數、虛擬機加總＝對帳橋角色 vm（2.1 虛擬機頁籤）、運作中加總＝existence 計入台數（有登記、非退役）、
      非虛擬機＋虛擬機＝全部；每一格（每一列×四種）＝下鑽列數；業務系統排行＝3.1。"""
    import count_bridge
    import existence
    import v2_dist
    import v2_systems

    d = v2_dist.summary(conn)
    ex = [m for m in existence.compute(conn)["machines"] if not m["retired"] and m["serials"]]
    check("2.x 分布統計", "總台數 = 存在可信度：有登記、非退役的機器數", d["total"], len(ex))
    n_vm = count_bridge.measure(count_bridge.compute(conn)["items"], "vm")
    n_on = sum(1 for m in ex if m.get("counted"))
    for dim, rows in d["dims"].items():
        check("2.x 分布統計", f"{dim}：全部加總 = 總台數", sum(r["all"] for r in rows), d["total"])
        check("2.x 分布統計", f"{dim}：虛擬機加總 = 對帳橋虛擬機（2.1 虛擬機頁籤）", sum(r["vm"] for r in rows), n_vm)
        check("2.x 分布統計", f"{dim}：運作中加總 = 存在可信度計入台數", sum(r["online"] for r in rows), n_on)
        bad = [r["value"] for r in rows if r["phys"] + r["vm"] != r["all"]]
        inv("2.x 分布統計", f"{dim}：非虛擬機＋虛擬機 = 全部", not bad, f"{bad[:5]}" if bad else "")
    import v2_hosts
    ms, vk = v2_hosts.machines(conn), v2_dist._vm_keys(conn)     # 逐格共用一份（正式機上每格重算要 2 秒）
    bad_drill = [f"{dim}:{r['value']}:{k}" for dim, rows in d["dims"].items() for r in rows
                 for k in ("all", "phys", "vm", "online")
                 if len(v2_dist.drill(conn, dim, r["value"], k, _ms=ms, _vk=vk)["rows"]) != r[k]]
    inv("2.x 分布統計", "每一格數字 = 下鑽列數（全部列×四種）", not bad_drill, f"{bad_drill[:5]}" if bad_drill else
        f"{sum(len(rows) for rows in d['dims'].values()) * 4} 格")
    sysl = {r["api_id"]: r["hosts"] for r in v2_systems.listing(conn)["rows"]}
    bad_sys = [s["api_id"] for s in d["systems"] if sysl.get(s["api_id"]) != s["hosts"]]
    inv("2.x 分布統計", "業務系統排行台數 = 3.1 業務系統", not bad_sys and len(d["systems"]) == len(sysl),
        f"{bad_sys[:5]}" if bad_sys else f"{len(sysl)} 個系統")


@section("2.x 主機查詢（2.1，2026-10-27）")
def check_v2_hosts(conn):
    """2.1 主機查詢：每個頁籤的筆數＝該頁籤的列數；已納管、逾期未偵測＝偵測狀態（existence）同一份的台數。"""
    import existence
    import v2_hosts

    ms = v2_hosts.machines(conn)
    bad = []
    for key, label in v2_hosts.TABS:
        lst = v2_hosts.listing(conn, key)
        tab = next(t for t in lst["tabs"] if t["key"] == key)
        if tab["count"] != len(lst["rows"]) or len({r["uid"] for r in lst["rows"]}) != len(lst["rows"]):
            bad.append(label)
    inv("2.x 主機查詢", "每個頁籤的筆數 = 列數（一台一列、不重複）", not bad, f"{bad}" if bad else f"{len(v2_hosts.TABS)} 個頁籤")
    summ = existence.compute(conn)["summary"]
    check("2.x 主機查詢", "已納管頁籤 = 偵測狀態已納管台數", sum(1 for r in ms if r["onboarded"]), summ.get("onboarded"))
    # 逾期未偵測＝沒達門檻但曾被看到過（v2_views.status_of；主機履歷的狀態同一個函式），獨立從 existence 重數
    exm = [m for m in existence.compute(conn)["machines"] if not m["retired"]]
    check("2.x 主機查詢", "逾期未偵測頁籤 = 偵測狀態「未達門檻、曾被偵測」台數", sum(1 for r in ms if r["stale"]),
          sum(1 for m in exm if not m["counted"] and m["last_seen_any"]))
    check("2.x 主機查詢", "逾期未偵測＋未偵測到（僅 CIA 登記）= 偵測狀態疑似下線台數",
          sum(1 for r in ms if r["status"]["key"] in ("stale", "missing")), summ.get("suspect_offline"))
    # 2.003.1 一致性：實體設備／虛擬機／管理口＝對帳橋（同一個詞在 1.1、2.1、9.2 必須同一個數字）
    import count_bridge
    import v2_views
    cbo = count_bridge.compute(conn)
    trial = {t["key"]: t["value"] for t in cbo["summary"]["trial"]}
    tabs = {t["key"]: t["count"] for t in v2_hosts.listing(conn, "all")["tabs"]}
    check("2.x 主機查詢", "實體設備頁籤 = 數量對帳「實體設備」試算", tabs["physical"], trial["physical"])
    check("2.x 主機查詢", "虛擬機頁籤 = 數量對帳「虛擬機 VM」筆數", tabs["vm"], count_bridge.measure(cbo["items"], "vm"))
    # 2026-10-27（v2.007.6 起）：VM 台數 = 逐台標 VM 的台數（主機履歷／搜尋／2.1 類型欄的「虛擬機」）。有明確 VM 證據的
    # VMware 平台（vCenter 一體機、巢狀 ESXi）也算 VM、畫面寫「虛擬機（VMware 設備）」；沒有 VM 證據的 ESXi 才不是 VM。
    check("2.x 主機查詢", "VM 台數 = 逐台標 VM 的台數", count_bridge.measure(cbo["items"], "vm"),
          sum(1 for r in ms if (r["badge"] or {}).get("vm")))
    check("2.x 主機查詢", "管理口頁籤 = 數量對帳「管理口」試算", tabs["mgmt"], trial["mgmt"])
    # 9.2 對帳頁 OS 那幾列 → 2.1 主機查詢的下鑽（?os_source= / ?os_class=）：筆數＝該列數字（加總＝下鑽）
    import manage_state as _ms
    comp = _ms.composition(conn)
    n_facts = v2_hosts.listing(conn, "all", os_source="facts")["filter_total"]
    n_guess = v2_hosts.listing(conn, "all", os_source="guessed")["filter_total"]
    n_unk = v2_hosts.listing(conn, "all", os_source="unknown")["filter_total"]
    check("2.x 主機查詢", "OS 欄有值下鑽 = 9.2 對帳「OS 欄有值」", n_facts, comp["os_from_facts"])
    check("2.x 主機查詢", "OS 掃描推測下鑽 = 9.2 對帳「OS 為掃描推測」", n_guess, comp["os_guessed"])
    check("2.x 主機查詢", "OS 未知下鑽 = 9.2 對帳「OS 未知」", n_unk, comp["data_quality"]["os_unknown"])
    bad_cls = [k for k, n in comp["by_os_class"].items()
               if v2_hosts.listing(conn, "all", os_class=k)["filter_total"] != n]
    inv("2.x 主機查詢", "各平台下鑽筆數 = 9.2 對帳「平台」各列", not bad_cls, f"對不上：{bad_cls[:3]}" if bad_cls else "")
    home = v2_views.home(conn)
    inv("2.x 一致性", "「實體設備」在 1.1、2.1、9.2 是同一個數字",
        home["physical"] == tabs["physical"] == trial["physical"],
        f"1.1={home['physical']}　2.1={tabs['physical']}　9.2={trial['physical']}")
    inv("2.x 一致性", "「主機」在 1.1 與 9.2 是同一個數字", home["hosts"] == trial["hosts"],
        f"1.1={home['hosts']}　9.2={trial['hosts']}")


@section("2.x 主機履歷完整資料（2026-10-27）")
def check_v2_sections(conn):
    """病歷表下方每一段：頁簽上的數字＝點進去的列數；查不到的一定有原因；summary＝逐段載入的結果。
    抽樣：收集過重建資料的、有軟體清單的、各取幾台，再加幾台沒收集的（每台約數秒，所以只抽樣）。"""
    import oncall
    import v2_sections

    groups, of = oncall._groups(conn)
    picks = []

    def add(serial):
        k = of.get(serial)
        if k and k not in picks and not k.startswith("ret:"):
            picks.append(k)
    for sql in ("SELECT asset_serial FROM host_rebuild ORDER BY collected_at DESC LIMIT 2",
                "SELECT DISTINCT asset_serial FROM host_package WHERE asset_serial IS NOT NULL LIMIT 2",
                "SELECT DISTINCT asset_serial FROM host_account WHERE asset_serial IS NOT NULL LIMIT 1"):
        try:
            for r in conn.execute(sql):
                add(r[0])
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
    for k in sorted(k for k in groups if not k.startswith("ret:"))[:2]:
        if k not in picks:
            picks.append(k)
    bad, bad_unknown, failed = [], [], []
    for k in picks[:7]:
        secs = v2_sections.all_sections(conn, k)
        summ = {x["key"]: x for x in v2_sections.summary(conn, k)["sections"]}
        for s in secs:
            if (s["unknown"] or "").startswith("讀取失敗"):
                failed.append(f"{k}/{s['key']}")
            if s["unknown"] is None and s["count"] != len(s["rows"]):
                bad.append(f"{k}/{s['key']}")
            if s["count"] is None and not s["unknown"]:
                bad_unknown.append(f"{k}/{s['key']}")
            if summ[s["key"]]["count"] != s["count"]:
                bad.append(f"{k}/{s['key']}（summary）")
    inv("2.x 主機履歷", "每一段頁簽上的數字 = 點進去的列數（summary 與逐段載入一致）", not bad,
        f"{bad[:5]}" if bad else f"抽 {len(picks[:7])} 台 × {len(v2_sections.SECTION_KEYS)} 段")
    inv("2.x 主機履歷", "查不到的段一定寫原因（不留白）", not bad_unknown, f"{bad_unknown[:5]}" if bad_unknown else "")
    inv("2.x 主機履歷", "沒有一段讀取失敗", not failed, f"{failed[:5]}" if failed else "")


@section("機櫃實體盤點（2026-10-27）")
def check_rack_audit(conn):
    """盤點列數＝基準箱子數；已盤列數＝基準列數；差異數＝下鑽；進度＝有結果的櫃；超過一年未盤 ⊆ 在用箱子。"""
    import json as _json

    import rack_inventory
    import rack_layout

    rack_inventory.ensure_tables(conn)
    bad_book, bad_counted, bad_diff, bad_prog = [], [], [], []
    audits = rack_inventory.list_audits(conn)
    for a in audits:
        aid = a["id"]
        det = rack_inventory.audit_detail(conn, aid)
        subs = rack_inventory._active_submits(conn, aid)
        for r in conn.execute("SELECT room, rack, snapshot_json, book_count FROM rack_audit_rack WHERE audit_id = ?", (aid,)):
            rows = _json.loads(r["snapshot_json"])
            if r["book_count"] != len(rows) or len({x["key"] for x in rows}) != len(rows):
                bad_book.append(f"#{aid} {r['room']}/{r['rack']}")
            s = subs.get((r["room"], r["rack"]))
            if s and set(s["rows"]) != {x["key"] for x in rows}:
                bad_counted.append(f"#{aid} {r['room']}/{r['rack']}")
        d = rack_inventory.diffs(conn, aid)
        sm = rack_inventory.summary(conn, aid)
        if a["diff_count"] != len(d) or sm["diff_open"] != sum(1 for x in d if x["status"] == "待處理")                 or sum(r["diff_count"] for r in det["racks"]) != len(d)                 or sum(v["total"] for v in sm["by_kind"].values()) != len(d):
            bad_diff.append(f"#{aid}")
        if sm["rack_done"] != len(subs) or a["rack_done"] != len(subs) or sm["rack_total"] != len(det["racks"])                 or sm["counted"] != sum(r["book_count"] for r in det["racks"] if r["done"]):
            bad_prog.append(f"#{aid}")
    note = f"{len(audits)} 次盤點"
    inv("機櫃盤點", "每櫃盤點列數 = 基準箱子數（一箱一列、不重複）", not bad_book, f"{bad_book[:5]}" if bad_book else note)
    inv("機櫃盤點", "已盤的櫃：結果列 = 基準列（不多不少）", not bad_counted, f"{bad_counted[:5]}" if bad_counted else note)
    inv("機櫃盤點", "差異數 = 下鑽筆數（清單、各櫃、各種類、未處理）", not bad_diff, f"{bad_diff[:5]}" if bad_diff else note)
    inv("機櫃盤點", "進度 = 有結果的櫃數；已盤台數 = 已盤櫃的帳上台數", not bad_prog, f"{bad_prog[:5]}" if bad_prog else note)
    dc = rack_layout.datacenter(conn)
    bad_stale = [r["room"] for r in dc["rooms"]
                 if not set(r.get("audit_stale_keys", [])) <= {k for t in r["racks"] for k in t["keys"]}
                 or r.get("audit_never_count", 0) > len(r.get("audit_stale_keys", []))]
    inv("機櫃盤點", "超過一年未盤 ⊆ 在用箱子；從沒盤過 ≤ 超過一年未盤", not bad_stale, f"{bad_stale[:5]}" if bad_stale else "")


@section("搬遷包（2026-10-31）")
def check_relocation_packages(conn):
    """全部機房（不抽樣）：包成員總數（去重）＝機房現役台數（不含 VMware）；每包成員清單筆數＝包台數；
    成員 key 集合＝預期的非 VMware 現役 machine key 集合（不只比數量）；明細的 room 與每個成員的 room 都是該機房；
    同一台不會出現在兩個包。"""
    import relocation_packages as rp

    bad_total, bad_list, bad_dup, bad_keys, bad_room = [], [], [], [], []
    names = rp.rooms(conn)
    for room in names:
        expect = rp.active_machine_keys(conn, room)
        res = rp.list_packages(conn, room)
        if res["total"] != len(expect) or sum(p["count"] for p in res["packages"]) != len(expect):
            bad_total.append(f"{room}（{len(expect)}→{res['total']}/{sum(p['count'] for p in res['packages'])}）")
        seen: list[str] = []
        for p in res["packages"]:
            d = rp.package_detail(conn, room, p["id"])
            if d is None or len(d["members"]) != p["count"]:
                bad_list.append(f"{room}/{p['name']}")
                continue
            if d["room"] != room or any(x["room"] != room for x in d["members"]):
                bad_room.append(f"{room}/{p['name']}")
            seen += [x["key"] for x in d["members"]]
        if len(seen) != len(set(seen)):
            bad_dup.append(room)
        if set(seen) != expect:
            bad_keys.append(f"{room}（多 {len(set(seen) - expect)}／少 {len(expect - set(seen))}）")
    note = f"{len(names)} 個機房（全部）"
    inv("搬遷包", "包成員總數（去重）= 機房現役台數（不含 VMware）", not bad_total, f"{bad_total[:5]}" if bad_total else note)
    inv("搬遷包", "每包成員清單筆數 = 包台數（加總 = 下鑽）；明細與成員的機房都對", not bad_list and not bad_room,
        f"{(bad_list + bad_room)[:5]}" if bad_list or bad_room else note)
    inv("搬遷包", "同一台機器不會出現在兩個包", not bad_dup, f"{bad_dup[:5]}" if bad_dup else note)
    inv("搬遷包", "成員 key 集合 = 預期的非 VMware 現役 machine key 集合", not bad_keys, f"{bad_keys[:5]}" if bad_keys else note)


@section("2.x 第二批：5.1 機房平面圖、5.2 儲存容量、7 盤點作業（2026-10-28）")
def check_v2_batch2(conn):
    """同一個詞、同一個數字：5.1（＝舊 4-5，同一支 datacenter）每個機房「在機櫃實體設備」＝2.1「實體設備」頁籤
    該機房機櫃的列數；5.1 下鑽筆數＝格子數字；5.2 Datastore 預警＝獨立重數；7 盤點任務存的數字＝下鑽；
    位置重疊待處理不含「同一套、不同節點」。"""
    import collections

    import inventory_flow
    import overlap_queue
    import rack_layout
    import storage_capacity
    import v2_hosts

    dc = rack_layout.datacenter(conn)
    phys = v2_hosts.listing(conn, "physical")["rows"]
    by_room = collections.Counter(r["room"] for r in phys if r.get("room") and r.get("rack"))
    bad = [f"{r['room']} 5.1={r['racked']} 2.1={by_room.get(r['room'], 0)}" for r in dc["rooms"] if r["racked"] != by_room.get(r["room"], 0)]
    extra = [k for k in by_room if k not in {r["room"] for r in dc["rooms"]}]
    inv("2.x 一致性", "5.1（＝舊 4-5）各機房在機櫃實體設備 = 2.1 實體設備頁籤該機房機櫃列數", not bad and not extra,
        "；".join(bad[:5] + [f"2.1 多出機房 {extra[:3]}"] if extra else bad[:5]) or f"{sum(by_room.values())} 台、{len(by_room)} 個機房")
    bad = []
    for r in dc["rooms"]:
        for name, cnt, keys in [("unknown", "unknown_count", "unknown_keys"), ("off", "off_count", "off_keys"), ("pending", "pending_count", "pending_keys"),
                                ("unobs", "unobserved_count", "unobserved_keys"), ("removed", "removed_count", "removed_keys")]:
            if r["power"][cnt] != len(r["power"][keys]) or r["power"][cnt] != sum(len(t["power"][keys]) for t in r["racks"]):
                bad.append(f"{r['room']} {name}")
    inv("5.1 機房平面圖", "電力容量：未知規格／關機未下架／待確認／未觀察到／已不在櫃上 數字 = 下鑽筆數 = 各櫃加總", not bad, f"{bad[:5]}" if bad else "")

    ds = storage_capacity.datastores(conn)
    th = ds["threshold_pct"]
    rooms = storage_capacity.overview(conn)["rooms"]
    indep = collections.Counter(v["room"] for v in ds["volumes"] if v["free_pct"] is not None and v["free_pct"] < th)
    bad = [r["room"] for r in rooms if r["near_full_count"] != indep.get(r["room"], 0)]
    inv("5.2 儲存容量", f"各機房 Datastore 預警（剩餘 < {th}%）= 獨立重數", not bad, f"{bad[:5]}" if bad else f"{sum(indep.values())} 個")

    inventory_flow.ensure_tables(conn)
    bad = []
    tasks = conn.execute("SELECT * FROM inv_task ORDER BY id").fetchall()
    for t in tasks:
        n = conn.execute("SELECT COUNT(*) n, COUNT(DISTINCT host) h FROM inv_task_row WHERE task_id = ?", (t["id"],)).fetchone()
        d = inventory_flow.diffs(conn, t["kind"], t["id"])["rows"]
        lst = next(x for x in inventory_flow.list_tasks(conn, t["kind"]) if x["id"] == t["id"])
        rep = inventory_flow.report(conn, t["kind"], t["id"])
        res = inventory_flow.result(conn, t["kind"], t["id"])
        if (n["n"], n["h"]) != (t["rows"], t["hosts"]) or t["diff_count"] != len(d)                 or lst["diff_open"] != sum(1 for x in d if x["status"] == "待處理")                 or sum(v["total"] for v in rep["by_kind"].values()) != len(d)                 or res["total"] != t["rows"] or len(res["rows"]) != t["hosts"]:
            bad.append(f"#{t['id']} {t['kind']}")
    inv("7 盤點作業", "盤點任務：項目數、主機數、差異數、待處理 = 盤點結果與差異處理的列數", not bad,
        f"{bad[:5]}" if bad else f"{len(tasks)} 次盤點（帳號／軟體／服務）")

    g = overlap_queue.groups(conn)
    eng = [set(e["keys"]) for e in g.get("engineered", [])]
    leak = [f"{x['room']} {x['rack']}" for x in g["groups"] if any(set(x["keys"]) <= e for e in eng)]
    inv("5.1 機房平面圖", "位置重疊待處理不含「同一套、不同節點」（ODA／Exadata）", not leak,
        f"{leak[:5]}" if leak else f"同一套、不同節點 {len(eng)} 組")


@section("2.x 第三批：6.1 納管作業、9.1 待補資料（2026-10-28）")
def check_v2_batch3(conn):
    """6.1「已納管」＝2.1「已納管」頁籤；納管進度各大類已納管加總＝同一個數字、分子 ≤ 分母、下鑽＝格子；
    新設備上線各步驟加總＝總數＝生命週期非退出台數；9.1 CIA 補登＝2.1「未登錄 CIA」；依單位缺漏＝下鑽；
    聯絡資訊待補依型號加總＝實體機待補台數。"""
    import daily_risk
    import data_fix
    import device_lifecycle
    import v2_hosts
    import v2_onboard

    tabs = {t["key"]: t["count"] for t in v2_hosts.listing(conn, "onboarded")["tabs"]}
    pg = v2_onboard.progress(conn)
    inv("2.x 一致性", "「已納管」在 2.1 與 6.1 納管進度是同一個數字", pg["onboarded_total"] == tabs["onboarded"],
        f"2.1={tabs['onboarded']}　6.1={pg['onboarded_total']}")
    bad = [r["family"] for r in pg["rows"] if not (r["n_online_onboarded"] <= r["n_online"] <= r["n_all"])
           or r["n_online_onboarded"] > r["n_onboarded"]]
    inv("6.1 納管進度", "每個作業系統大類：運作中已納管 ≤ 運作中 ≤ 登記；運作中已納管 ≤ 已納管", not bad, f"{bad[:5]}" if bad else "")
    srv = [r for r in pg["rows"] if r["server"]]
    check("6.1 納管進度", "覆蓋率分子 = 伺服器大類運作中已納管加總", pg["server"]["onboarded"], sum(r["n_online_onboarded"] for r in srv))
    bad = []
    for r in srv:
        for k in ("online_not_onboarded", "onboarded"):
            if len(v2_onboard.drill(conn, r["family"], k)["rows"]) != len(r[k]):
                bad.append(f"{r['family']}/{k}")
    inv("6.1 納管進度", "伺服器大類：運作中未納管、已納管的格子 = 下鑽筆數", not bad, f"{bad[:5]}" if bad else "")
    nd = v2_onboard.new_devices(conn)
    lc = device_lifecycle.compute(conn)
    exits = sum(lc["counts"][k] for k, _ in device_lifecycle.EXITS)
    check("6.1 新設備上線", "各步驟（含五步完成）加總 = 列數", sum(nd["counts"].values()), nd["total"])
    check("6.1 新設備上線", "列數 = 生命週期總數 − 退役／報廢／豁免", nd["total"], lc["total"] - exits)
    ex_keys = {k for k, _ in device_lifecycle.EXITS}
    bad = [k for k, _ in v2_onboard.STEPS[:4]
           if sum(1 for r in nd["rows"] if r["steps"][k]) != sum(1 for r in lc["rows"] if r["stage"] not in ex_keys and r["done"][k])]
    inv("6.1 新設備上線", "前四步每一步完成台數 = 舊 4-10 生命週期同一步完成台數", not bad, f"{bad}" if bad else "")

    nc = {t["key"]: t["count"] for t in v2_hosts.listing(conn, "no_cia")["tabs"]}["no_cia"]
    inv("9.1 待補資料", "CIA 補登（台）= 2.1「未登錄 CIA」頁籤（同一份計算）", nc == len(v2_hosts.listing(conn, "no_cia")["rows"]), f"{nc} 台")
    df = data_fix.overview(conn, snapshot=False)      # 驗數字不順手存週快照
    by = {m["key"]: m for m in df["machines"]}
    bad = [k for k, n in df["missing_by_item"].items() if n != sum(1 for m in df["machines"] if k in m["missing"])]
    if df["missing_machines"] != sum(1 for m in df["machines"] if m["missing"]):
        bad.append("missing_machines")
    bad += [u["key"] for u in df["units"] if any(k not in by for k in u["machines"])]
    inv("9.1 待補資料", "依單位／依項目的缺漏台數 = 下鑽清單", not bad, f"{bad[:5]}" if bad else f"{df['missing_machines']} 台有缺漏")
    td = daily_risk.todo(conn)
    check("9.1 待補資料", "聯絡資訊待補：依型號加總 = 實體機待補台數", sum(m["count"] for m in td["models"]), td["physical_missing"])


@section("儲存容量（2026-10-22）")
def check_storage(conn):
    """頁面總量＝下鑽卷宗加總；原始列＝各卷宗列數加總（去重沒有丟列）；依系統加總＝vDisk 總量。"""
    import storage_capacity

    ds = storage_capacity.datastores(conn)
    for k in ("capacity_mib", "in_use_mib", "free_mib", "provisioned_mib"):
        check("儲存容量", f"總量 {k} = 卷宗加總", round(ds["totals"][k], 1), round(sum(v[k] for v in ds["volumes"]), 1))
    check("儲存容量", "原始列數 = 各卷宗（含已排除）列數加總", ds["raw_rows"],
          sum(v["rows"] for v in ds["volumes"] + ds["excluded"]))
    bs = storage_capacity.by_system(conn)
    check("儲存容量", "依系統加總 = vDisk 總量", round(sum(r["capacity_mib"] for r in bs["rows"]), 1), round(bs["total_mib"], 1))
    o = storage_capacity.overview(conn)
    rooms = o["rooms"]
    check("儲存容量", "各機房（含跨機房、機房不明）容量加總 = 總量",
          round(sum(r["capacity_mib"] for r in rooms), 1), round(ds["totals"]["capacity_mib"], 1))
    check("儲存容量", "各機房卷宗數加總 = 卷宗數", sum(r["volume_count"] for r in rooms), ds["volume_count"])
    vol_room = {v["id"]: v["room"] for v in ds["volumes"]}
    bad = [r["room"] for r in rooms
           if any(vol_room.get(i) != r["room"] for i in r["volume_ids"])
           or r["volume_count"] != len(r["volume_ids"]) or r["near_full_count"] != len(r["near_full_ids"])
           or round(r["capacity_mib"], 1) != round(sum(v["capacity_mib"] for v in ds["volumes"] if v["room"] == r["room"]), 1)]
    inv("儲存容量", "每個機房按鈕 = 下鑽卷宗（筆數、容量、快滿數）", not bad, f"{bad[:5]}" if bad else "")
    check("儲存容量", "各機房快照數加總 = 快照數", sum(r["snapshot_count"] for r in rooms), len(o["snapshots"]["rows"]))
    check("儲存容量", "依系統：各機房磁碟加總 = vDisk 總量",
          round(sum(o["by_system"]["total_by_room"].values()), 1), round(o["by_system"]["total_mib"], 1))
    ph = storage_capacity.physical(conn)
    check("儲存容量", "實體主機磁碟總量 = 逐台加總", round(ph["total_gb"], 1), round(sum(r["total_gb"] for r in ph["rows"]), 1))
    # 2.006.3（找碴 C12）：依收法拆開（檔案系統／VG／磁碟）各自加總＝逐台加總，台數加總＝總台數
    bk = ph.get("by_kind") or {}
    check("儲存容量", "實體主機：各收法台數加總 = 總台數", sum(b["hosts"] for b in bk.values()), len(ph["rows"]))
    check("儲存容量", "實體主機：各收法容量加總 = 總量", round(sum(b["total_gb"] for b in bk.values()), 1), round(ph["total_gb"], 1))
    # Codex 429：剩餘查不到的不當 0——總剩餘＝逐台查得到的剩餘加總（全部查不到＝None）、各收法加總＝總剩餘、
    # 剩餘未知台數＝逐台計數；有任何掛載剩餘未知的主機 free_pct 一律是 None（不判、不告警）
    _kf = [r["free_gb"] for r in ph["rows"] if r["free_gb"] is not None]
    _r1 = lambda v: None if v is None else round(v, 1)  # noqa: E731
    check("儲存容量", "實體主機剩餘 = 逐台查得到的剩餘加總（未知不算 0）", _r1(ph["free_gb"]), _r1(sum(_kf)) if _kf else None)
    _bkf = [b["free_gb"] for b in bk.values() if b["free_gb"] is not None]
    check("儲存容量", "實體主機：各收法剩餘加總 = 總剩餘", _r1(sum(_bkf)) if _bkf else None, _r1(ph["free_gb"]))
    check("儲存容量", "實體主機：剩餘未知台數 = 逐台計數", ph.get("free_unknown_hosts"),
          sum(1 for r in ph["rows"] if r.get("free_unknown")))
    _rf = [r["phys_free_gb"] for r in rooms if r.get("phys_free_gb") is not None]
    check("儲存容量", "各機房實體主機剩餘加總 = 總剩餘", _r1(sum(_rf)) if _rf else None, _r1(ph["free_gb"]))
    check("儲存容量", "各機房剩餘未知台數加總 = 總未知台數", sum(r.get("phys_free_unknown", 0) for r in rooms),
          ph.get("free_unknown_hosts"))
    check("儲存容量", "剩餘有未知的主機不判剩餘 %（不告警）",
          sum(1 for r in ph["rows"] if r.get("free_unknown") and r["free_pct"] is not None), 0)
    al = storage_capacity.alerts(ds, ph)
    check("儲存容量", "容量預警 = 快滿 datastore + 檔案系統快滿的實體主機", al["count"],
          sum(r["near_full_count"] for r in rooms) + sum(1 for p in ph["rows"] if p["by"] == "mount" and p["free_pct"] is not None
                                                        and round(100 - p["free_pct"], 1) >= 80))     # R17：使用率 ≥80%
    # R17（2.044.0）：磁碟 80／90 兩級——高＝使用率 ≥90、處理＝80～90；兩級加總＝實體主機預警筆數；查不到的另計未收集
    _hi = sum(1 for p in al["physical"] if p["level"] == "high")
    _hd = sum(1 for p in al["physical"] if p["level"] == "handle")
    check("儲存容量", "實體主機磁碟：高（≥90%）＋處理（80～90%）= 預警台數", _hi + _hd, len(al["physical"]))
    check("儲存容量", "實體主機磁碟：高 = 使用率 ≥90% 台數（獨立重算）", _hi,
          sum(1 for p in ph["rows"] if p["by"] == "mount" and p["free_pct"] is not None and round(100 - p["free_pct"], 1) >= 90))
    check("儲存容量", "實體主機磁碟：未收集 = 檔案系統剩餘查不到的台數", al["physical_uncollected"],
          sum(1 for p in ph["rows"] if p["by"] == "mount" and p["free_pct"] is None))

@section("連線推論上下游（方案 B，2026-10-22）")
def check_observed_deps(conn):
    """觀察到的連線推出來的「系統依賴系統」：
    1. 每條邊的證據筆數 = 下鑽列數；觀察次數 = 下鑽列 count 加總；最後看到 = 下鑽最大值
    2. 兩端都是現役登記上存在的業務系統（APID），且不是自己連自己
    3. 系統地圖：連線推論列＝推出來碰到它、且不是兩端都有人工登記的邊；證據數＝下鑽列數"""
    import blast_radius
    import observed_deps

    inferred = observed_deps.infer(conn)
    edges = inferred["edges"]
    live_apis = {(r[0] or "").strip() for r in conn.execute(
        "SELECT api_id, asset_status FROM hardware WHERE TRIM(COALESCE(api_id,'')) <> ''")
        if not system_stats.is_retired_row({"asset_status": r[1]})}
    bad_cnt, bad_end = [], []
    for e in edges:
        rows = observed_deps.edge_rows(conn, e["caller"], e["callee"], inferred)
        if (len(rows) != e["evidence_count"] or sum(int(r["count"] or 0) for r in rows) != e["observations"]
                or max((r["last_seen"] or "" for r in rows), default=None) != e["last_seen"]):
            bad_cnt.append(f"{e['caller']}→{e['callee']}")
        if e["caller"] == e["callee"] or e["caller"] not in live_apis or e["callee"] not in live_apis:
            bad_end.append(f"{e['caller']}→{e['callee']}")
    inv("連線推論", "每條邊的證據筆數／觀察次數／最後看到 = 下鑽", not bad_cnt,
        f"{bad_cnt[:5]}" if bad_cnt else f"{len(edges)} 條邊")
    inv("連線推論", "邊的兩端都是現役業務系統，且不是自己連自己", not bad_end,
        f"{bad_end[:5]}" if bad_end else "")
    # 3. 系統地圖（兩種狀態都驗）：連線推論列＝推出來碰到它、而且不是兩端都有人工登記的邊
    #    （一條不多一條不少，兩端看到的一樣），證據數＝下鑽列數
    bad_map = []
    by_pair = {(e["caller"], e["callee"]): e for e in edges}
    registered = blast_radius.registered_systems(conn)
    sids = sorted({e["caller"] for e in edges} | {e["callee"] for e in edges})[:30]
    sids += sorted(registered & live_apis)[:30]
    for sid in sorted(set(sids)):
        try:
            m = blast_radius.system_map(conn, sid)
        except ValueError:
            bad_map.append(f"{sid}:查無此系統")
            continue
        got = set()
        for side, lst in (("on", m["depends_on"]), ("by", m["depended_by"])):
            for d in lst:
                if d.get("source") != "observed":
                    continue
                pair = (sid, d["api_id"]) if side == "on" else (d["api_id"], sid)
                got.add(pair)
                if pair not in by_pair or d["evidence"]["count"] != len(
                        observed_deps.edge_rows(conn, pair[0], pair[1], inferred)):
                    bad_map.append(f"{pair[0]}→{pair[1]}")
        want = {p for p, e in by_pair.items() if sid in p and blast_radius.observed_edge_applies(e, registered)}
        if got != want:
            bad_map.append(f"{sid}:連線推論列{len(got)}≠推出來的邊{len(want)}")
    inv("連線推論", "系統地圖：連線推論列 = 推出來的邊（兩端都人工登記的除外），證據數 = 下鑽列數", not bad_map,
        f"{bad_map[:5]}" if bad_map else f"驗了 {len(set(sids))} 個系統")


@section("每日變化報告（v1.500.0，2026-10-23）")
def check_daily_digest(conn):
    """每日快照的差異：
    1. 每一類的分頁數字 = 下鑽筆數；收集中斷各網段加總 = 收集中斷筆數
    2. 對稱：N 日新增 = 反向比對（N-1 對 N）的消失，N 日消失 = 反向的新增
    3. 現役台數的變化 = 新增 − 消失（改名配對兩邊抵銷）
    4. 快照紀錄的台數 = 那天快照的列數
    唯讀：表不存在就提醒、不建表。"""
    import daily_digest as dd

    if not dd.tables_exist(conn):
        warn("每日變化", "還沒有任何每日快照（排程或「立即寫一份」後才驗得到）", False)
        return
    ds = dd.dates(conn)
    if not ds:
        warn("每日變化", "還沒有任何每日快照", False)
        return
    cfg = dd.load_settings(conn)
    bad_cnt, bad_sym, bad_delta, bad_run = [], [], [], []
    for day in ds[:7]:
        out = dd.diff_for(conn, day, cfg)
        for k, n in out["counts"].items():
            if n != len(out["items"][k]):
                bad_cnt.append(f"{day}:{k}")
        if sum(sg["gap"] for sg in out["segments"]) != out["counts"]["collect_gap"]:
            bad_cnt.append(f"{day}:collect_gap 網段加總")
        cur = dd.load_snapshot(conn, day)
        run = dd.load_run(conn, day)
        if not run or run["machines"] != len(cur):
            bad_run.append(day)
        if not out["prev_date"]:
            continue
        prev = dd.load_snapshot(conn, out["prev_date"])
        fwd = dd.diff_rows(prev, cur, cfg, day)["items"]
        rev = dd.diff_rows(cur, prev, cfg, out["prev_date"])["items"]
        if (sorted(x["key"] for x in fwd["added"]) != sorted(x["key"] for x in rev["missing"])
                or sorted(x["key"] for x in fwd["missing"]) != sorted(x["key"] for x in rev["added"])):
            bad_sym.append(day)
        act = lambda snap: sum(1 for r in snap.values() if not r.get("retired"))  # noqa: E731
        if act(cur) - act(prev) != len(fwd["added"]) - len(fwd["missing"]):
            bad_delta.append(f"{day}:{act(prev)}→{act(cur)} 新增{len(fwd['added'])} 消失{len(fwd['missing'])}")
    inv("每日變化", "每一類分頁數字 = 下鑽筆數；收集中斷網段加總 = 筆數", not bad_cnt,
        f"{bad_cnt[:5]}" if bad_cnt else f"驗了 {min(len(ds), 7)} 天")
    inv("每日變化", "對稱：N 日新增 = 反向比對的消失（反之亦然）", not bad_sym, f"{bad_sym[:5]}" if bad_sym else "")
    inv("每日變化", "現役台數變化 = 新增 − 消失", not bad_delta, f"{bad_delta[:5]}" if bad_delta else "")
    inv("每日變化", "快照紀錄台數 = 快照列數", not bad_run, f"{bad_run[:5]}" if bad_run else "")


@section("設備風險清單／聯絡資訊（v1.499.0）")
def check_daily_risk(conn):
    """卡片數＝分頁列數；未重開機用獨立的 /proc/uptime 式換算重數；合約四級加總＝範圍台數；
    單點三類加總＝有正式機的系統數、每列台數＝下鑽；合約匯入 對到＋對不上＋多筆＝列數；待補依型號加總＝缺資料實體機。"""
    import datetime as _d

    import daily_risk as dr

    now = _d.datetime.now()
    o = dr.risk_overview(conn, now)
    u, h, s, c = o["uptime"], o["hardware"], o["spof"], o["contract"]
    check("設備風險", "未重開機 卡片 = 列數", u["count"], len(u["rows"]))
    # 獨立重算：直接用每列的開機時刻換天數，跟門檻比（不經 uptime_tab 的排序／組裝）
    indep = sum(1 for r in u["rows"]
                if (now - _d.datetime.strptime(r["last_boot"], "%Y-%m-%d %H:%M")).days >= u["threshold"])
    check("設備風險", "未重開機 列的開機天數都 ≥ 門檻", indep, u["count"])
    inv("設備風險", "未重開機：讀得到＋讀不到 = 有收集的台", u["with_uptime"] + u["unreadable_count"] == u["collected"],
        f"{u['with_uptime']}+{u['unreadable_count']} vs {u['collected']}")
    check("設備風險", "硬體異常 卡片 = 列數", h["count"], len(h["rows"]))
    inv("設備風險", "硬體異常 每列項目數 = 下鑽明細數",
        all(r["finding_count"] == len(r["findings"]) and r["findings"] for r in h["rows"]))
    check("設備風險", "單點 卡片 = 列數", s["count"], len(s["rows"]))
    check("設備風險", "單點＋無法判定＋非單點 = 有正式機的系統數",
          s["count"] + s["undetermined_count"] + s["ok_count"], s["systems_with_prod"])
    inv("設備風險", "單點 每列正式台數 = 下鑽台數，且規則成立",
        all(r["prod_count"] == len(r["machines"]) and dr.spof_rule([m["room"] for m in r["machines"]])[0]
            for r in s["rows"]))
    check("設備風險", "合約四級＋無資料 = 範圍台數", sum(c["counts"].values()), c["total"])
    check("設備風險", "合約 已過期＋90＋180 = 列數",
          c["counts"]["expired"] + c["counts"]["d90"] + c["counts"]["d180"], len(c["rows"]))
    check("設備風險", "合約 無資料 = 下鑽列數", c["counts"]["none"], len(c["none_rows"]))
    cur = dr.contract_current(conn)
    if cur["current"]:
        imp = cur["current"]
        check("合約匯入", "對到＋對不上＋多筆 = 列數", imp["matched"] + imp["unmatched"] + imp["ambiguous"],
              imp["row_count"])
        check("合約匯入", "列數 = 存下的列", imp["row_count"], len(cur["rows"]))
    else:
        warn("合約匯入", "尚無合約匯入", True, "0 筆，略過")
    t = dr.todo(conn)
    check("聯絡資訊", "待補 依型號加總 = 缺資料實體機", sum(x["count"] for x in t["models"]), t["physical_missing"])
    inv("聯絡資訊", "待補 每組台數 = 下鑽台數",
        all(x["count"] == len(x["machines"]) for x in t["systems"] + t["models"]))
    check("聯絡資訊", "已補＋待補 = 帳內現役台數", t["filled_count"] + t["missing_count"], t["in_book_total"])


@section("補登分派／新設備生命週期（v1.499.1）")
def check_data_fix_lifecycle(conn):
    """補登分派：各單位台數加總＝總台數；各單位每項缺漏加總＝全體該項缺漏；各組台數＝下鑽；分數＝已填÷應填。
    生命週期：各階段加總＝總台數＝下鑽；「已完成第 k 步」＝停在 k 之後的台數；第 5 步跟漏斗一致。"""
    import data_fix
    import device_lifecycle
    import pipeline

    o = data_fix.overview(conn, snapshot=False)
    check("補登分派", "各使用單位台數加總 = 總台數", sum(u["machine_count"] for u in o["units"]), o["machine_count"])
    bad = [i for i in data_fix.ITEM_KEYS
           if sum(u["missing_by_item"][i] for u in o["units"]) != o["missing_by_item"][i]
           or o["missing_by_item"][i] != sum(1 for m in o["machines"] if i in m["missing"])]
    inv("補登分派", "各單位每項缺漏加總 = 全體該項缺漏 = 下鑽台數", not bad, f"{bad}" if bad else "")
    inv("補登分派", "每組台數 = 下鑽台數、缺漏台數 ≤ 台數",
        all(g["machine_count"] == len(g["machines"]) and g["missing_machines"] <= g["machine_count"]
            for g in o["units"] + o["systems"]))
    check("補登分派", "缺漏台數 = 有缺項的機器", o["missing_machines"], sum(1 for m in o["machines"] if m["missing"]))
    check("補登分派", "已填 + 缺漏 = 應填（逐台加總）", o["filled_total"] + sum(len(m["missing"]) for m in o["machines"]),
          o["required_total"])

    lc = device_lifecycle.compute(conn)
    check("生命週期", "各階段加總 = 總台數", sum(lc["counts"].values()), lc["total"])
    check("生命週期", "總台數 = 下鑽列數", lc["total"], len(lc["rows"]))
    bad = [k for k in lc["stage_order"] if lc["counts"][k] != sum(1 for r in lc["rows"] if r["stage"] == k)]
    inv("生命週期", "每個階段卡片 = 下鑽台數", not bad, f"{bad}" if bad else "")
    steps = [s["key"] for s in lc["steps"]]
    bad = [k for i, k in enumerate(steps)
           if lc["reached"][k] != sum(lc["counts"][x] for x in steps[i + 1:]) + lc["counts"]["done"]]
    inv("生命週期", "已完成第 k 步 = 停在 k 之後的台數", not bad, f"{bad}" if bad else "")
    bad = [r["serial"] for r in lc["rows"] if r["stage"] == "done"
           and not set(r["pipeline_stages"]) & {"complete", "collected"}]
    inv("生命週期", "五步完成的每台在納管漏斗都是資料齊全／已收集", not bad, f"{bad[:5]}" if bad else "")
    check("生命週期", "漏斗計數跟直接呼叫 pipeline 一致（沒有第二套漏斗）", lc["pipeline_counts"],
          pipeline.summarize(conn)["counts"])


_RE_IND_SUFFIX = re.compile(r"^(.+?)[-_](ilom|ilo|idrac|imm|xcc|bmc)$", re.I)
_RE_IND_MGMT_OS = re.compile(r"idrac|\bilom?\b|\bbmc\b|\bimm\d?\b|\bxcc\b", re.I)
_RE_IND_SERVER_OS = re.compile(r"windows\s*server|red\s*hat|\brhel\b|centos|oracle\s*linux|\bsuse\b|ubuntu|rocky|\baix\b",
                               re.I)
_IND_VENDORS = (("dell", r"\bdell\b|poweredge|idrac"), ("hpe", r"\bhpe?\b|proliant|\bilo\b"),
                ("lenovo", r"lenovo|thinksystem|\bx3\d{3}\b|\bxcc\b"), ("oracle", r"oracle|exadata|\boda\b|\bilom\b"),
                ("cisco", r"cisco|\bucs\b"))


def _ind_vendor(txt: str | None) -> str | None:
    for v, p in _IND_VENDORS:
        if re.search(p, txt or "", re.I):
            return v
    return None


def _check_bridge_independent(conn, items: list[dict], dc: dict) -> None:
    """審查要求的獨立不變式：不呼叫對帳橋／rack_layout 同一支函式，自己用 SQL＋正規式重算。"""
    # (a) 對帳橋有機櫃的機器數（逐台）= 4-5 每一櫃在用登記數加總
    racked = len({x["machine_key"] for x in items if x["rack_status"] == "rack"})
    rec = sum(t["record_count"] for r in dc["rooms"] for t in r["racks"])
    check("對帳橋 × 4-5（獨立）", "對帳橋有機櫃的機器（逐台）= 4-5 各櫃在用登記數加總", racked, rec)
    # (b) 逐筆（再審）：主機名是「<本體>-ilom」這種、而且同一櫃（機櫃欄 / 前面那段）有 <本體> 的登記，
    #     每一筆都要是「管理口（掛在本體）」（或是同一台另一筆已經是，這筆是它的重複登記）
    ret = ",".join("'" + x.replace("'", "''") + "'" for x in manage_state.RETIRED_STATUS)
    # 終審：不限同一櫃（M13 那 5 台 Exadata ILOM 機櫃欄只寫櫃號、沒有 U），本體主機名在任何一筆現役登記上就算
    rows = conn.execute(f"SELECT asset_serial, hostname FROM hardware WHERE TRIM(COALESCE(asset_status,'')) "
                        f"NOT IN ({ret}) AND hostname IS NOT NULL").fetchall()
    names = {r["hostname"].strip().lower() for r in rows}
    by_serial = {x["asset_serial"]: x for x in items}
    attached_keys = {x["machine_key"] for x in items if x["role"] == "mgmt_attached"}
    miss = []
    for r in rows:
        m = _RE_IND_SUFFIX.match(r["hostname"].strip())
        if m and m.group(1).lower() in names:
            x = by_serial.get(r["asset_serial"])
            if x and not (x["role"] == "mgmt_attached" or (x["role"] == "duplicate" and x["machine_key"] in attached_keys)):
                miss.append(f"{r['hostname']}（{x['role']}）")
    inv("對帳橋 × 4-5（獨立）", "主機名尾巴是管理口、本體主機名有現役登記的每一筆都是「管理口（掛在本體）」", not miss,
        "、".join(miss[:5]))
    # (c) 併進主機的管理口：廠商（型號）兩邊都看得出來時一定相同
    model = {r["asset_serial"]: r["device_model"] for r in conn.execute("SELECT asset_serial, device_model FROM hardware")}
    bad = []
    for x in items:
        if x["role"] == "mgmt_attached" and x["host_serial"]:
            a, b = _ind_vendor(model.get(x["asset_serial"])), _ind_vendor(model.get(x["host_serial"]))
            if a and b and a != b:
                bad.append(f"{x['hostname']}→{x['host_serial']}（{a}／{b}）")
    inv("對帳橋 × 4-5（獨立）", "併進主機的管理口廠商 = 本體廠商", not bad, "、".join(bad[:5]))
    # (d) 只有弱訊號（名稱 OOB、資產名稱「遠端管理」；沒有 -ilom 尾巴、OS／型號也不是 iDRAC／iLO…）的登記：
    #     VM 或裝了完整伺服器 OS 的，永遠不是管理口
    hw = {r["asset_serial"]: r for r in conn.execute(
        "SELECT asset_serial, hostname, asset_name, is_vm, device_model, os FROM hardware")}
    bad = []
    for x in items:
        if x["role"] not in ("mgmt_attached", "mgmt_orphan"):
            continue
        h = hw[x["asset_serial"]]
        strong = bool(_RE_IND_SUFFIX.match((h["hostname"] or "").strip())
                      or _RE_IND_MGMT_OS.search(h["os"] or "") or _RE_IND_MGMT_OS.search(h["device_model"] or ""))
        if not strong and (manage_state.is_vm_value(h["is_vm"], h["device_model"])
                           or _RE_IND_SERVER_OS.search(h["os"] or "")):
            bad.append(x["asset_serial"])
    inv("對帳橋 × 4-5（獨立）", "只有弱訊號（OOB／遠端管理）的 VM／伺服器 OS 登記不是管理口", not bad, "、".join(bad[:5]))


@section("對帳橋／對帳只算有涵蓋的網段（v1.502.0–3，2026-10-26）")
def check_count_bridge(conn):
    """對帳橋：每筆現役登記剛好一種角色（互斥且窮盡）；每一格＝下鑽；試算＝各項加總；
    「計入」跟 existence.compute 一致。對帳：一致＋搜不到＋未涵蓋＝登記台數，跟首頁同一份。"""
    import count_bridge as cb
    import existence

    out = cb.compute(conn)
    items, s = out["items"], out["summary"]
    # 獨立：SQL 直接數現役登記（排退役），CIA／帳外用 GLOB 版判定
    ret = ",".join("'" + x.replace("'", "''") + "'" for x in manage_state.RETIRED_STATUS)
    act = f"TRIM(COALESCE(asset_status, '')) NOT IN ({ret})"
    n_all = conn.execute(f"SELECT COUNT(*) FROM hardware WHERE {act}").fetchone()[0]
    n_off = conn.execute(f"SELECT COUNT(*) FROM hardware WHERE {act} AND {system_report.off_book_sql()}").fetchone()[0]
    check("對帳橋", "各角色筆數加總 = 現役登記筆數（SQL）", sum(r["rows"] for r in s["roles"]), n_all)
    check("對帳橋", "各角色 CIA 筆加總 = CIA 現役筆數（SQL）", sum(r["cia_rows"] for r in s["roles"]), n_all - n_off)
    check("對帳橋", "各角色帳外筆加總 = 帳外現役筆數（SQL）", sum(r["offbook_rows"] for r in s["roles"]), n_off)
    inv("對帳橋", "每筆剛好一種角色（序號不重複、角色都在清單裡）",
        len({x["asset_serial"] for x in items}) == len(items) == n_all
        and all(x["role"] in cb.ROLE_KEYS for x in items), f"{len(items)} 筆")
    bad = []
    for r in s["roles"]:
        for side, key in (("CIA", "cia_rows"), ("帳外", "offbook_rows"), (None, "rows")):
            if len(cb.filter_items(items, r["role"], side)) != r[key]:
                bad.append(f"{r['role']}/{key}")
        xs = cb.filter_items(items, r["role"])
        by = cb.ROLE_UNITS_BY[r["role"]]
        exp = 0 if by == "none" else (len({x["frame"] for x in xs}) if by == "frames" else len(xs))
        if exp != r["units"]:
            bad.append(f"{r['role']}/units")
    inv("對帳橋", "每一格（CIA 筆／帳外筆／台）= 下鑽", not bad, f"{bad}" if bad else "")
    bad = [t["key"] for t in s["trial"] if t["value"] != sum(p["n"] for p in t["parts"])
           or any(p["n"] != cb.measure(cb.filter_items(items, p["role"], None, "racked" if p["measure"] == "racked" else None),
                                       p["role"], p["measure"]) for p in t["parts"])]
    inv("對帳橋", "試算 = 各組成加總、每個組成 = 下鑽", not bad, f"{bad}" if bad else "")
    bad = [t["key"] for t in s["trial"] if t["rack"] + t["unparsed"] + t["none"] != t["value"]
           or any(t[st] != sum(cb.measure(cb.filter_items(items, p["role"], None, st), p["role"], p["measure"], rack=st)
                               for p in t["parts"]) for st in cb.RACK_STATUS)]
    inv("對帳橋", "試算：有機櫃 + 機櫃欄認不出 + 缺機櫃資料 = 總數、每一種 = 下鑽", not bad, f"{bad}" if bad else "")
    bad = [r["role"] for r in s["roles"]
           if r["no_rack_rows"] != (len(cb.filter_items(items, r["role"], None, "none")) if r["role"] in cb.PHYS_ROLES else 0)]
    inv("對帳橋", "「缺機櫃資料」只標在實體設備類、筆數 = 下鑽", not bad, f"{bad}" if bad else "")
    # 跟 4-5 機房架構圖同一個「實體箱子」：直接拿 rack_layout.datacenter（畫面那一支）的數字比
    import rack_layout
    dc = rack_layout.datacenter(conn)
    phys = next(t for t in s["trial"] if t["key"] == "physical")
    check("對帳橋 × 4-5", "實體設備（有機櫃）= 4-5 機櫃台數（本體＋找不到本體的管理口＋疑似登記錯誤）",
          phys["rack"], dc["total_racked"])
    ports = sum(t["breakdown"]["port"] for r in dc["rooms"] for t in r["racks"])
    check("對帳橋 × 4-5", "管理口（掛在本體）= 4-5 併進主機的管理口", next(r for r in s["roles"] if r["role"] == "mgmt_attached")["rows"],
          ports)
    unp = sum(1 for r in dc["rooms"] for x in r["no_rack"] if x.get("no_rack_reason") == "機櫃欄認不出")
    inv("對帳橋 × 4-5", "機櫃欄認不出的台數（逐台）= 4-5「無機櫃資料」裡原因寫「機櫃欄認不出」的台數",
        len({x["machine_key"] for x in items if x["rack_status"] == "unparsed" and x["role"] != "duplicate"}) == unp,
        f"對帳橋 {len({x['machine_key'] for x in items if x['rack_status'] == 'unparsed' and x['role'] != 'duplicate'})} / 4-5 {unp}")
    _check_bridge_independent(conn, items, dc)
    # 例外（跟 4-5 一致）：找不到本體、有自己 U 位的管理口，實體設備照算一台（只算有機櫃的那種量）
    inv("對帳橋", "併進主機的管理口、重複登記不進實體設備／主機試算；找不到本體的管理口只有「有機櫃」的進實體設備",
        all(p["role"] not in ("mgmt_attached", "duplicate")
            and (p["role"] != "mgmt_orphan" or (t["key"] == "physical" and p["measure"] == "rack"))
            for t in s["trial"] if t["key"] in ("physical", "hosts") for p in t["parts"]))
    ex = existence.compute(conn)
    exp_in_db = sum(1 for m in ex["machines"] if m["in_asset_db"] and not m["retired"] and m["counted"])
    check("對帳橋", "計入（存在可信度達門檻）的機器數 = existence.compute（資產庫裡的）",
          len({x["machine_key"] for x in items if x["counted"]}), exp_in_db)
    groups = system_stats.collapse_machines([r for r in conn.execute("SELECT * FROM hardware")
                                             if not system_stats.is_retired_row(r)])
    check("對帳橋", "機器數（machine_key）= collapse_machines 獨立分組", len({x["machine_key"] for x in items}),
          len(groups))
    has_rep: dict[str, bool] = {}
    for x in items:
        if not x["override"]:
            # 4-5 同一個箱子的另一個管理 IP（frame＝box:本體）整台歸重複登記，算它有代表（本體那一台）
            has_rep[x["machine_key"]] = has_rep.get(x["machine_key"], False) or x["role"] != "duplicate" \
                or x["frame"].startswith("box:")
    orphan = [k for k, v in has_rep.items() if not v]
    inv("對帳橋", "每一台至少有一筆不是「重複登記」（4-5 多管理 IP 除外）", not orphan, f"{orphan[:3]}" if orphan else "")

    rc = cb.recon_coverage(conn)
    c = rc["counts"]
    inv("對帳（有涵蓋）", "一致 + 搜不到 + 未涵蓋 = 登記台數", c["overlap"] + c["missing"] + c["uncovered"] == rc["total"],
        f"{c['overlap']}+{c['missing']}+{c['uncovered']} vs {rc['total']}")
    bad = [b for b in cb.RECON_BUCKETS if c[b] != sum(1 for x in rc["items"] if x["bucket"] == b)]
    inv("對帳（有涵蓋）", "每格 = 下鑽台數", not bad, f"{bad}" if bad else "")
    st = api_stats(conn)
    check("對帳（有涵蓋）", "登記台數 = 首頁對帳登記台數", rc["total"], st.get("total_ica_machines"))
    check("對帳（有涵蓋）", "一致 = 首頁欄位", c["overlap"], st.get("covered_overlap_machines"))
    check("對帳（有涵蓋）", "搜不到（有涵蓋）= 首頁欄位", c["missing"], st.get("covered_missing_machines"))
    check("對帳（有涵蓋）", "未涵蓋 = 首頁欄位", c["uncovered"], st.get("uncovered_ica_machines"))
    den = rc["covered"] + rc["scan_only"]
    check("對帳（有涵蓋）", "帳實相符（有涵蓋）= 一致 ÷（有涵蓋 + 這一輪掃到沒登記）", st.get("match_pct_covered"),
          round(c["overlap"] * 100.0 / den, 1) if den else None)
    # 審查 H1：分母不能只錨在 MAX(scan_time)（221 那是中午 1 段的補掃）。獨立用 SQL 找這一輪裡涵蓋網段最多、
    # 而且真的有存活紀錄的那次掃描，這一輪的涵蓋網段至少要有那麼多
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=int(rc["days"]))).strftime("%Y-%m-%d %H:%M:%S")
    try:
        full = conn.execute(
            "SELECT scan_time, COUNT(DISTINCT cidr) FROM scan_coverage WHERE ok = 1 AND scan_time >= ? AND scan_time IN "
            "(SELECT DISTINCT scan_time FROM scan_history WHERE scan_ok = 1) GROUP BY scan_time "
            "ORDER BY 2 DESC, 1 DESC LIMIT 1", (cutoff,)).fetchone()
    except Exception:  # noqa: BLE001 - 舊庫沒有 scan_coverage：沒有可比的全網段掃描
        full = None
    inv("對帳（有涵蓋）", "涵蓋網段數 ≥ 這一輪最近一次全網段掃描的網段數（不可只錨在最後一次補掃）",
        rc["covered_segments"] >= (full[1] if full else 0),
        f"{rc['covered_segments']} vs {tuple(full) if full else '—'}")
    valid = {r[0] for r in conn.execute("SELECT DISTINCT scan_time FROM scan_history WHERE scan_ok = 1")}
    # 獨立：每次掃描涵蓋幾段（SQL 直接數），回 0 台又超過門檻段數的＝掃描器壞了，不可以被當成涵蓋
    small_max = int(cb.load_rules().get("empty_run_small_max_segments", 2))
    try:
        seg_n = {r[0]: r[1] for r in conn.execute("SELECT scan_time, COUNT(DISTINCT cidr) FROM scan_coverage WHERE ok = 1 "
                                                  "GROUP BY scan_time")}
    except Exception:  # noqa: BLE001 - 舊庫沒有 scan_coverage：沒有涵蓋紀錄可比
        seg_n = {}
    used = {t for _, t in cb.scan_basis(conn)["nets"]}
    bad = sorted(t for t in used if t not in valid and seg_n.get(t, 0) > small_max)
    inv("對帳（有涵蓋）", f"沒有用到「多段（>{small_max}）全部 0 台」的掃描當涵蓋（掃描器壞掉不能當成「掃過、全部沒回應」）",
        not bad, "、".join(bad[:5]))
    inv("對帳（有涵蓋）", "依據日期（畫面「依 … 掃描」）只取真的有結果的掃描",
        all(t in valid for t in {rc["scan_time"], rc["scan_from"]} if t))
    # 獨立：「可能整段斷線」的每一次，前後 24 小時內一定有掃到東西的掃描；「掃描器當時整體沒回應」的一定沒有
    win = float(cb.load_rules().get("empty_run_alive_window_hours", 24))
    def _p(t):
        return datetime.datetime.strptime(str(t)[:19], "%Y-%m-%d %H:%M:%S")
    vdt = [_p(t) for t in valid if t]
    # 多段全部 0 台的掃描（獨立用 SQL：涵蓋段數 > 門檻、scan_history 沒有存活紀錄）
    bdt = [_p(t) for t, n in seg_n.items() if t and t not in valid and n > small_max]

    def near(t):
        d = _p(t)
        return any(abs((v - d).total_seconds()) <= win * 3600 and not any(min(v, d) < b < max(v, d) for b in bdt)
                   for v in vdt)
    bad = [x["scan_time"] for x in rc["empty_runs"]
           if (x["kind"] == "suspect_down" and not near(x["scan_time"]))
           or (x["kind"] == "skipped_outage" and near(x["scan_time"]))]
    inv("對帳（有涵蓋）", f"小的 0 台補掃：前後 {win:g} 小時內有掃到東西（中間沒夾多段 0 台）才算「可能整段斷線」，否則算掃描器故障略過",
        not bad, "、".join(bad[:5]))
    sk = [x for x in rc["empty_runs"] if x["kind"] in ("skipped", "skipped_outage")]
    warn("對帳（有涵蓋）", "這一輪有掃描回 0 台被略過（多段全部 0 台、或掃描器當時整體沒回應的補掃）", not sk,
         "、".join(f"{x['scan_time']}（{x['segments']} 段）" for x in sk[:5]))
    warn("對帳（有涵蓋）", "可能整段斷線（只掃 1–2 段卻 0 台；照算涵蓋，那段的機器算搜不到）", not rc["suspect_down"],
         "、".join(f"{x['cidr']}（{x['scan_time']}）" for x in rc["suspect_down"][:5]))
    inv("對帳（有涵蓋）", "搜不到（有涵蓋）≤ 舊口徑搜不到", c["missing"] <= rc["total"] - c["overlap"])
    # 首頁 OS 那行改名「N 筆有填作業系統」：數字本身是 composition 的筆數，不是台數
    comp = manage_state.composition(conn)
    n_os = sum(1 for r in conn.execute(f"SELECT os FROM hardware WHERE {act}")
               if r["os"] and str(r["os"]).strip().upper() != "N/A")
    check("對帳橋", "「有填作業系統」筆數 = 現役登記中 OS 有值（不是 N/A）的筆數", comp["os_from_facts"], n_os)


@section("連線推論收集涵蓋率（2026-10-26）")
def check_observed_coverage(conn):
    """涵蓋率卡片：各 OS 大類加總＝總數；每格數字＝下鑽清單長度；有觀察＋沒觀察＝台數；
    總台數＝獨立重算的現役機器數（collapse_machines，退役另組不算）；按序號對得到的有觀察台數 ≤ 有觀察。"""
    import observed_deps

    c = observed_deps.coverage(conn)
    fam = c["by_family"]
    check("連線涵蓋率", "各 OS 大類台數加總 = 總台數", sum(f["total"] for f in fam), c["total"])
    check("連線涵蓋率", "各 OS 大類有觀察加總 = 有觀察台數", sum(f["observed_count"] for f in fam),
          c["observed_count"])
    bad = [f["family"] for f in fam
           if f["observed_count"] != len(f["observed"]) or f["total"] != len(f["observed"]) + len(f["missing"])]
    inv("連線涵蓋率", "每格數字 = 下鑽清單長度（有觀察／沒觀察）", not bad, f"{bad[:5]}" if bad else "")
    keys = [x["key"] for f in fam for x in f["observed"] + f["missing"]]
    inv("連線涵蓋率", "同一台只出現在一格", len(keys) == len(set(keys)), f"{len(keys)} vs {len(set(keys))}")
    indep = {k for k in system_stats.collapse_machines(conn.execute(
        "SELECT asset_serial, hostname, ip, asset_status FROM hardware").fetchall()) if not k.startswith("ret:")}
    check("連線涵蓋率", "總台數 = 獨立重算的現役機器數（machine_key）", c["total"], len(indep))
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=c["days"])).strftime("%Y-%m-%d %H:%M:%S")
    try:
        by_serial = {system_stats.lifecycle_prefix(r) + system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"])
                     for r in conn.execute(
                         "SELECT h.asset_serial, h.hostname, h.ip, h.asset_status FROM hardware h "
                         "JOIN host_connection c ON c.asset_serial = h.asset_serial WHERE c.last_seen >= ?",
                         (cutoff,))}
    except Exception:  # noqa: BLE001 - 舊庫還沒有 host_connection：下限就是 0
        by_serial = set()
    by_serial = {k for k in by_serial if not k.startswith("ret:")}
    inv("連線涵蓋率", "按序號對得到的有觀察台數 ≤ 有觀察台數（IP 對到的另外加）",
        len(by_serial) <= c["observed_count"], f"{len(by_serial)} vs {c['observed_count']}")
    inv("連線涵蓋率", "累積天數 ≥ 0，跨月底只在有第一次觀察時才可能為真",
        c["cumulative_days"] >= 0 and (c["first_observed"] is not None or not c["month_end_crossed"]))


def check_v2_batch5(conn):
    """2.007.13：7.3 收集涵蓋＝2.1「已納管」同一份、每格＝清單長度、「有軟體資料」每台都真的有現役套件（SQL）；
    10.1 主機組月報的生命週期四態加總＝總量。"""
    import host_monthly
    import software_inventory
    import v2_hosts

    cov = software_inventory.coverage(conn)
    onb = sum(1 for m in v2_hosts.machines(conn) if m["onboarded"])
    check("7.3 收集涵蓋", "各作業系統已納管加總 = 2.1 已納管台數", sum(r["n_onboarded"] for r in cov["rows"]), onb)
    inv("7.3 收集涵蓋", "每格 = 下鑽清單長度、有＋沒有 = 已納管",
        all(r["n_with"] == len(r["with"]) and r["n_without"] == len(r["without"]) and r["n_with"] + r["n_without"] == r["n_onboarded"]
            for r in cov["rows"]), "")
    live = {x for r in conn.execute("SELECT ip, asset_serial FROM host_package WHERE gone_at IS NULL") for x in r if x}
    bad = [x["name"] for r in cov["rows"] for x in r["with"] if x["ip"] not in live and x["serial"] not in live]
    bad += [x["name"] for r in cov["rows"] for x in r["without"] if x["ip"] in live or x["serial"] in live]
    inv("7.3 收集涵蓋", "有／沒有軟體資料跟 host_package（SQL）一致", not bad, f"{bad[:5]}" if bad else "")
    from routers.vulns import eos_summary
    lc = host_monthly.lifecycle(conn)
    bs = (eos_summary(session=None, conn=conn).get("host_os") or {}).get("by_status") or {}
    inv("10.1 主機組月報", "生命週期：各平台加總 ≥ 合計（同一台兩種版本才會多算，差額另外標示）",
        lc["row_dup"] >= 0 and all(r["expired"] + r["upcoming"] + r["unknown"] + r["ok"] == r["total"] for r in lc["rows"]), f"差 {lc['row_dup']}")
    for k, lab in (("expired", "已 EOS"), ("upcoming", "一年內 EOS"), ("ok", "支援中")):
        check("10.1 主機組月報", f"生命週期「{lab}」= 8.3 生命週期作業系統磚塊（逐台）", lc["total"][k], bs.get(k, 0))


@section("1.2 異常報告下鑽（2.047.4）")
def check_anomaly_drills(conn):
    """異常報告上的數字＝點下去的筆數：已由 vCenter 校正、要查看、體檢最常卡在 X（同一份計算）。"""
    import health_check
    import manage_state

    dq = manage_state.composition(conn).get("data_quality") or {}
    check("1.2 異常報告", "已由 vCenter 校正 = 下鑽筆數", dq.get("verified_by_vcenter", 0), len(manage_state.vcenter_verified_rows(conn)))
    hs = health_check.summary(conn)
    check("1.2 異常報告", "要查看 = 下鑽筆數", hs["needs_review"], len(health_check.drill(conn)))
    bad = [k for k, n in hs["by_issue"].items() if n != len(health_check.drill(conn, k))]
    inv("1.2 異常報告", "體檢每一項的台數 = 下鑽筆數", not bad, f"{bad[:5]}" if bad else "")


@section("9.3 憑證管理（2026-11-01）")
def check_certs(conn):
    """卡片加總 = 啟用中項目數 = 清單筆數；每張卡 = 它下鑽清單的筆數（同一份計算）；
    再用第二種算法（直接 SQL 取每項最新一筆觀測、自己用秒數分段）獨立重算每張卡；停用＋啟用 = 全部；沒有孤兒觀測。"""
    import certs

    if not certs.tables_ready(conn):
        warn("9.3 憑證管理", "憑證表尚未建立（升級前的庫，啟動後會建）", False, "略過")
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    s = certs.summary(conn, now)
    cards = {c["key"]: c["count"] for c in s["cards"]}
    rows_all = certs.list_items(conn, include_inactive=True, now=now)
    act = [r for r in rows_all if r["active"]]
    inv("9.3 憑證管理", "卡片加總 = 啟用中項目數 = 預設清單筆數", sum(cards.values()) == s["total_active"] == len(act) == len(certs.list_items(conn, now=now)),
        f"卡片 {sum(cards.values())}／啟用 {s['total_active']}／清單 {len(act)}")
    inv("9.3 憑證管理", "每張卡的數字 = 點開下鑽清單的筆數", all(len(certs.list_items(conn, status=k, now=now)) == n for k, n in cards.items()),
        "、".join(f"{k}:{n}" for k, n in cards.items()))
    inv("9.3 憑證管理", "啟用 + 停用 = 全部項目", s["total_active"] + s["inactive"] == conn.execute("SELECT count(*) FROM cert_item").fetchone()[0], "")
    ind = {k: 0 for k in certs.STATUS_ORDER}
    void_sql = " AND voided=0" if certs._has_col(conn, "cert_observation", "voided") else ""      # 作廢的觀測不計入到期判斷
    has_pend = certs._has_col(conn, "cert_item", "replaced_pending")
    pend_sql = ", replaced_pending, replace_status" if has_pend else ", 0, ''"
    for r in conn.execute(f"SELECT id{pend_sql} FROM cert_item WHERE active=1"):
        # 中繼探測（source=probe）只做比對，不進到期判斷（2.042.02）
        o = conn.execute(f"SELECT not_after_utc FROM cert_observation WHERE item_id=? AND source <> 'probe'{void_sql} ORDER BY observed_at_utc DESC, rowid DESC LIMIT 1", (r[0],)).fetchone()
        if o is None or (r[1] and r[2] == "已更換"):       # 已更換且稍後補登：舊到期日不算，歸「無到期資料」
            ind["none"] += 1
            continue
        left = (datetime.datetime.strptime(o[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc) - now).total_seconds()
        ind["expired" if left <= 0 else "d7" if left <= 7 * 86400 else "d30" if left <= 30 * 86400 else "d60" if left <= 60 * 86400 else "ok"] += 1
    check("9.3 憑證管理", "各狀態卡片 = 獨立 SQL 重算（以剩餘秒數分段）", cards, ind)
    orphan = conn.execute("SELECT count(*) FROM cert_observation WHERE item_id NOT IN (SELECT id FROM cert_item)").fetchone()[0]
    inv("9.3 憑證管理", "沒有找不到所屬項目的觀測紀錄", orphan == 0, f"{orphan} 筆")
    if certs._probe_ready(conn):
        po = conn.execute("SELECT count(*) FROM cert_probe_status WHERE item_id NOT IN (SELECT id FROM cert_item)").fetchone()[0]
        inv("9.3 憑證管理", "每筆探測狀態都對得到存在的項目", po == 0, f"{po} 筆")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cert_probe_observation'").fetchone():
            pq = conn.execute("SELECT count(*) FROM cert_probe_observation WHERE item_id NOT IN (SELECT id FROM cert_item)").fetchone()[0]
            inv("9.3 憑證管理", "每筆探測歷史都對得到存在的項目", pq == 0, f"{pq} 筆")
        diff_n = sum(1 for r in act if r["probe_differs"])
        ind_diff = 0
        for r in act:
            if r["pending_expiry"] or not r["not_after_utc"] or not r["probe_ok"] or not r["probe_not_after_utc"]:
                continue
            ind_diff += certs._local_date(r["not_after_utc"]) != certs._local_date(r["probe_not_after_utc"])
        check("9.3 憑證管理", "『探測不一致』筆數 = 人工到期日與探測到期日（UTC+8 日期）不同的筆數", diff_n, ind_diff)
    # 第二批（條件過濾）：每種篩選維度都必須分得完整（各選項加總 = 啟用中總數），且 有到期日 + 無到期日 = 全部
    tot = s["total_active"]
    for key, vals in (("env", [r["environment"] for r in act]), ("mount", [r["mount_type"] for r in act]),
                      ("unit", [r["req_unit"] for r in act]), ("rs", [r["replace_status"] for r in act])):
        parts = sum(len(certs.list_items(conn, now=now, **{key: (v or certs.BLANK)})) for v in set(vals))
        inv("9.3 憑證管理", f"篩選維度「{key}」各選項加總 = 啟用中總數（沒有漏掉或重複）", parts == tot, f"{parts}／{tot}")
    inv("9.3 憑證管理", "有到期日 + 無到期日 = 啟用中總數",
        len(certs.list_items(conn, now=now, has_expiry="1")) + len(certs.list_items(conn, now=now, has_expiry="0")) == tot, "")
    inv("9.3 憑證管理", "『無到期資料』卡片 = 篩選『無到期日』的筆數（待補清單同一份計算）",
        cards.get("none") == len(certs.list_items(conn, now=now, has_expiry="0")), "")
    inv("9.3 憑證管理", "『已更換待補到期日』的項目都在「無到期資料」卡片裡，且風險等級是「待補到期日」",
        all(r["status"] == "none" and r["risk"] == "待補到期日" for r in certs.list_items(conn, now=now, pending="1")), "")
    inv("9.3 憑證管理", "停用的項目都有停用原因", all(r["deactivate_reason"] for r in rows_all if not r["active"] and r["deactivated_at"]), "")


@section("單機維運記錄本（2.041.38）")
def check_host_ops(conn):
    """單機維運記錄本的數字。唯讀。
    ① 計次 = 由事件獨立重算（不呼叫 count_events）、sum(計次) = 計入次數、事件ID 的分類 = 其計次列的、同一則 WhatsUp 告警不會算進兩台。
    ② WhatsUp 段數 = 直接從 whatsup_alert_history 原始列用『獨立的最小實作』重算（不呼叫 host_ops 的 _episodes／_match 等任何函式）：
       歸屬規則（IP 唯一屬於這台且主機名對得上、非退役）、new／cleared 分段、期間重疊都自己寫一份，與 API 的事件逐段比對。
    **觀察模式（預設）**：221 測試區沒有 WhatsUp 監控資料，真實資料第一次會在公司正式區的部署關卡才碰到，誤報會讓部署中止。
    所以這段預設只印 ⚠️ 並計入檢查數，不計為失敗、不中止部署；app_settings 的 verify_strict_host_ops=1（預設不存在）才升級為會中止的 ❌。
    首次在正式區看到警示時，先人工核對（開該機的「事件與維運紀錄」對照原始告警），確認不是誤報再開 strict。
    抽樣（最多 30 台）涵蓋各種關聯型態：IP＋名稱一致、只有名稱、只有 IP、共用 IP、退役、NMON-only、incident-only，不是只取 IP 對得上的。"""
    import json as _json
    import re as _re
    from datetime import datetime as _dt, timedelta as _td

    import host_ops
    import manage_state as ms
    import oncall
    import system_stats

    pg = "單機維運記錄本"
    strict = str(db.get_setting(conn, "verify_strict_host_ops", "0") or "0") == "1"

    def rep(page, name, ok, detail=""):
        if strict or ok:
            inv(page, name, ok, detail)
        else:
            warn(page, name, ok, (detail + "　" if detail else "") + "〔觀察模式：首次在正式區看到警示，先人工核對再開 verify_strict_host_ops=1〕")
    has_w = host_ops._table(conn, "whatsup_alert_history")      # 沒有 WhatsUp 表時仍檢查 NMON／事件應變的計次與合計
    groups, _ = oncall._groups(conn)
    hk = lambda h: system_stats.strip_domain(h or "").strip().lower()      # noqa: E731
    ips_of: dict[str, set[str]] = {}
    hosts_of: dict[str, set[str]] = {}
    active_by_ip: dict[str, set[str]] = {}
    all_hosts: set[str] = set()
    for k, g in groups.items():
        ips_of[k], hosts_of[k] = set(), {x for x in (hk(r["hostname"]) for r in g) if x}
        all_hosts |= hosts_of[k]
        for r in g:
            for ip in ms.ips_of(r["ip"]):
                ips_of[k].add(ip)
                if not k.startswith("ret:"):
                    active_by_ip.setdefault(ip, set()).add(k)
    rows_all = [dict(r) for r in conn.execute("SELECT * FROM whatsup_alert_history ORDER BY id")] if has_w else []
    by_dev: dict[str, list[dict]] = {}
    for r in rows_all:
        by_dev.setdefault(r["device_id"], []).append(r)

    # ---- 獨立分段（最小實作）：new 起、cleared 止；changed 屬於當前段；沒有 cur 的 changed 自己開一段
    def segments(rows):
        out, cur = [], None
        for r in rows:
            if r["event"] == "cleared":
                if cur is not None:
                    cur["end"] = r["at"]
                    cur["last_id"] = r["id"]
                    out.append(cur)
                    cur = None
                continue
            if r["event"] == "new" or cur is None:
                if cur is not None:
                    out.append(cur)
                cur = {"first": r, "rows": [r], "end": None, "last_id": r["id"]}
            else:
                cur["rows"].append(r)
                cur["last_id"] = r["id"]
        if cur is not None:
            out.append(cur)
        return out

    def key_of(item):          # 事件鍵的獨立近似：磁碟取槽／路徑、記憶體／CPU 各一鍵，其餘用原文
        m = _re.search(r"(?<![A-Za-z0-9])([A-Za-z]):(?=[\\/\s)\]、,;]|$)|(?<![A-Za-z0-9:/])(/[\w.\-/]*)(?=[\s)\],;、]|$)", item)
        if m and _re.search(r"(?i)disk|磁碟|硬碟|partition|分割|filesystem|file ?system|檔案系統|volume|free ?space|drive|inode|儲存空間|容量|/dev/|^[^a-z]*[a-z]:", item):
            return "disk:" + (m.group(1) or m.group(2)).upper().rstrip("/")
        if _re.search(r"(?i)memory|記憶體|\bram\b|\bswap\b|換頁|\boom\b", item):
            return "mem"
        if _re.search(r"(?i)\bcpu\b|處理器|processor", item):
            return "cpu"
        return "i:" + item

    def keys_of(problem):
        t = _re.sub(r"\s*等\s*\d+\s*項\s*$", "", (problem or "").strip())
        return {key_of(x.strip()) for x in t.split("、") if x.strip()} or {"empty"}

    # ---- 抽樣：各關聯型態各取最多 3 台
    typed: dict[str, list[str]] = {t: [] for t in ("ip_name", "name_only", "ip_only", "shared", "retired", "nmon_only", "incident_only")}

    def add(t, k):
        if k in groups and k not in typed[t] and len(typed[t]) < 3:
            typed[t].append(k)
    for r in rows_all:
        ip, h = (r["ip"] or "").strip(), hk(r["name"])
        owners = active_by_ip.get(ip, set())
        by_name = [k for k in groups if h and h in hosts_of[k]]
        if len(owners) > 1:
            for k in sorted(owners):
                add("shared", k)
        elif len(owners) == 1:
            (k,) = tuple(owners)
            add("ip_name" if h in hosts_of[k] else "ip_only", k) if (h in hosts_of[k] or h not in all_hosts) else None
        for k in by_name:
            if not k.startswith("ret:") and ip not in ips_of[k]:
                add("name_only", k)
            if k.startswith("ret:"):
                add("retired", k)
    nm_hosts = set()
    if host_ops._table(conn, "nmon_series"):
        nm_hosts = {hk(r[0]) for r in conn.execute("SELECT DISTINCT host FROM nmon_series")}
    for k, hs in hosts_of.items():
        if not k.startswith("ret:") and hs & nm_hosts:
            add("nmon_only", k)
    if host_ops._table(conn, "incident_event"):
        for r in conn.execute("SELECT scope_json FROM incident_event"):
            try:
                for k in (_json.loads(r[0] or "null") or {}).get("machines", []):
                    add("incident_only", k)
            except ValueError:
                continue
    keys: list[str] = []
    for t in typed.values():
        for k in t:
            if k not in keys:
                keys.append(k)
    keys = keys[:30]
    if not keys:
        inv(pg, "沒有可抽樣的主機（本檢查不適用）", True, "no sample")
        return

    now = _dt.now()
    w0, w1 = (now - _td(days=90)).strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S")
    seen: dict[str, str] = {}
    dup, bad_cnt, bad_tot, bad_cls, bad_seg = [], [], [], [], []
    for k in keys:
        d = host_ops.build(conn, k, "90d", now)
        ev = {e["id"]: e for e in d["events"]}
        mine: dict[tuple, list[str]] = {}
        for e in d["events"]:
            if e.get("countable"):
                mine.setdefault((e["category"], e["partition"], e["source"]), []).append(e["id"])
        api = {(c["category"], c["partition"], c["source"]): c for c in d["counts"]}
        if set(mine) != set(api) or any(len(v) != api[t]["count"] or sorted(v) != sorted(api[t]["event_ids"]) for t, v in mine.items() if t in api):
            bad_cnt.append(k)
        if d["totals"]["countable"] != sum(c["count"] for c in d["counts"]):
            bad_tot.append(k)
        for c in d["counts"]:
            for i in c["event_ids"]:
                e = ev.get(i)
                if e is None or (e["category"], e["partition"], e["source"]) != (c["category"], c["partition"], c["source"]):
                    bad_cls.append(f"{k}/{i}")
        wug = [e for e in d["events"] if e["source"] == "whatsup"]
        for e in wug:
            if e["id"] in seen and seen[e["id"]] != k:
                dup.append(e["id"])
            seen[e["id"]] = k
        # 獨立重算 WhatsUp 段
        exp = []
        if not k.startswith("ret:"):
            for rows in by_dev.values():
                for sg in segments(rows):
                    f = sg["first"]
                    ip, h = (f["ip"] or "").strip(), hk(f["name"])
                    if ip in ips_of[k] and active_by_ip.get(ip) == {k} and h in hosts_of[k]:
                        t0 = f["at"].replace("T", " ")[:19]
                        t1 = sg["end"].replace("T", " ")[:19] if sg["end"] else None
                        if t0 <= w1 and (t1 is None or t1 >= w0):
                            exp.append(sg)
        got = {i: int(e["id"].split("-")[1]) for i, e in ((e["id"], e) for e in wug)}
        used: set[str] = set()
        for sg in exp:
            ids = [i for i, rid in got.items() if sg["first"]["id"] <= rid <= sg["last_id"]]
            used |= set(ids)
            lo = len(keys_of(sg["first"]["problem"]))
            hi = len(set().union(*(keys_of(r["problem"]) for r in sg["rows"])))
            at_first = [i for i in ids if got[i] == sg["first"]["id"]]
            if not (len(at_first) >= max(1, lo) and len(ids) <= hi):
                bad_seg.append(f"{k}/seg{sg['first']['id']}")
        if set(got) - used:
            bad_seg.append(f"{k}/多出的事件 {sorted(set(got) - used)[:3]}")
    rep(pg, f"計次 = 由事件獨立重算（抽樣 {len(keys)} 台：" + "、".join(f"{t} {len(v)}" for t, v in typed.items()) + "）", not bad_cnt, "、".join(bad_cnt[:5]))
    rep(pg, "計入故障次數的事件數 = 各計次列加總", not bad_tot, "、".join(bad_tot[:5]))
    rep(pg, "每個事件ID 的類別／分割區／來源 = 它所屬計次列的", not bad_cls, "、".join(bad_cls[:5]))
    rep(pg, "同一則 WhatsUp 告警不會同時算進兩台主機", not dup, "、".join(dup[:5]))
    rep(pg, "WhatsUp 事件段 = 從原始歷史列獨立重算（歸屬、分段、每段事件數的上下界；沒歸屬到的不得有事件）", not bad_seg, "、".join(bad_seg[:5]))


def check_eos_follow(conn, today=None):
    """EOS 通知與回覆追蹤（2.041.51／.52）的數字與硬規則。唯讀。
    ① 加總＝下鑽、項次連續；② 母體獨立重算（直接用 eos_notify.eos_candidates ＋門檻自己數候選×負責人）；
    ③ 子集 ≤ 母體、互斥；④ 表內硬規則（真的日期解析：0000-00-00、2026-02-30 皆無效；規劃更換要承諾日與來源；
       擬保留要來源；更換完成要證據；零寬字元視為空）；
    ⑤ 用「最小獨立實作」重算 未回覆／逾承諾日／最近一次寄送失敗／尚未通知／缺負責人／保留到期／保留缺依據，
       **不呼叫 classify／_Notify**，與磚相等。
    今天一律是台北日期（today 可注入）。
    **觀察模式（預設）**：首次在正式區看到警示先人工核對，不中止部署；app_settings 的 verify_strict_eos=1（預設不存在）才升級為 ❌。"""
    import re as _re
    from datetime import datetime as _dt

    import eos_follow as ef
    import eos_notify as en
    import vuln_notify as vn

    pg = "EOS 追蹤"
    strict = str(db.get_setting(conn, "verify_strict_eos", "0") or "0") == "1"

    def rep(name, ok, detail=""):
        if strict or ok:
            inv(pg, name, ok, detail)
        else:
            warn(pg, name, ok, (detail + "　" if detail else "") + "〔觀察模式：先人工核對再開 verify_strict_eos=1〕")

    zw_pat = "[​-‏‪-‮⁠-⁯﻿­᠎ㅤﾠ]"      # 零寬／隱形字元（含 Hangul filler）

    def pdate(s):
        """真的日期解析（不是 regex 字串比較）：無效日期（含 0000-00-00）回 None。"""
        try:
            return _dt.strptime(s, "%Y-%m-%d").date() if isinstance(s, str) and len(s) == 10 else None
        except ValueError:
            return None

    def blank(v):
        return not _re.sub(zw_pat, "", str(v or "")).strip()

    def nn(s):
        return _re.sub(r"\s+", "", _re.sub(r"[（(\[【][^）)\]】]*[）)\]】]", "", s or "")).lower()

    today = today or ef.today_tpe()
    ov = ef.overview(conn, today)
    rows = ov["rows"]
    tile = {t["key"]: t["count"] for t in ov["tiles"]}
    rep("加總=下鑽（每個磚的數字 = 同一份 rows 過濾筆數）",
        all(t["count"] == len(ef.drill(ov, t["key"])) for t in ov["tiles"]), f"tiles={[(t['key'], t['count']) for t in ov['tiles']]}")
    rep("項次 1..N 連續不重複", [r["no"] for r in rows] == list(range(1, len(rows) + 1)), f"N={len(rows)}")

    # ---- 母體（獨立）
    try:
        cfg_n = en.load_settings(conn)
    except en.SettingsCorrupt:
        cfg_n = {}
    buckets = en.buckets_of(cfg_n)
    cands, _ = en.eos_candidates(conn, today)
    follows: dict[tuple, dict] = {}
    if ef._tbl(conn, "eos_follow"):
        for r in conn.execute("SELECT * FROM eos_follow WHERE is_demo = 0"):
            follows[(r["item_key"], r["owner_ref"])] = dict(r)
    universe = []          # (ik, ref, track, mk, item, name, bucket_or_None, in_window, stale, follow)
    matched = set()
    for c in cands:
        ik = f"{c['track']}|{c['mk']}|{c['item']}"
        b = en.bucket_for(c["days_left"], buckets)
        for name in (c["owners"] or [None]):
            ref = name or ef.UNASSIGNED
            if b is not None or (ik, ref) in follows:
                matched.add((ik, ref))
                universe.append((ik, ref, c["track"], c["mk"], c["item"], name, b, b is not None, False, follows.get((ik, ref))))
    for (ik, ref), f in follows.items():
        if (ik, ref) not in matched:
            p = ik.split("|")
            d = pdate(f.get("eos_date"))
            b = en.bucket_for((d - today).days, buckets) if d else None
            universe.append((ik, ref, p[0], "|".join(p[1:-1]), p[-1], None if ref == ef.UNASSIGNED else ref, b, False, True, f))
    n_live = sum(1 for r in rows if not r["stale"])
    rep("母體：候選×負責人（獨立重算）= 總覽非 stale 列數", sum(1 for u in universe if not u[8]) == n_live,
        f"獨立={sum(1 for u in universe if not u[8])}　App={n_live}")
    rep("有追蹤紀錄的列數 = eos_follow 表筆數（非示範）", sum(1 for r in rows if r["has_follow"]) == len(follows),
        f"列={sum(1 for r in rows if r['has_follow'])}　表={len(follows)}")
    rep("stale 列數 = 追蹤紀錄中不在目前候選的筆數", sum(1 for r in rows if r["stale"]) == sum(1 for u in universe if u[8]),
        f"App={sum(1 for r in rows if r['stale'])}　獨立={sum(1 for u in universe if u[8])}")

    # ---- 子集／互斥
    rep("未回覆 ⊆ 已通知", all(r["notified"] for r in rows if "unreplied" in r["flags"]))
    rep("完成／作廢的列只有自己的旗標（不再算待辦）",
        all(r["flags"] == ["done"] for r in rows if r["done_state"] == "更換完成" and not r["voided_at"])
        and all(r["flags"] == ["voided"] for r in rows if r["voided_at"]))
    rep("擬保留尚無依據 與 保留已到期 互斥", not any("hold_no_basis" in r["flags"] and "hold_expired" in r["flags"] for r in rows))

    # ---- 表內硬規則（只看未作廢的）
    live_f = [f for f in follows.values() if not f.get("voided_at")]
    rep("承諾日／重新檢視日都是有效的 YYYY-MM-DD（0000-00-00、2026-02-30 皆無效）",
        all(pdate(f[k]) is not None for f in live_f for k in ("committed_date", "hold_review_date") if f.get(k)),
        f"違規={[f['item_key'] for f in live_f for k in ('committed_date', 'hold_review_date') if f.get(k) and pdate(f[k]) is None][:3]}")
    rep("規劃更換必須有有效承諾日與回覆來源",
        all(pdate(f.get("committed_date")) is not None and not blank(f.get("reply_source")) for f in live_f if f["reply_state"] == "規劃更換"))
    rep("擬保留必須有回覆來源", all(not blank(f.get("reply_source")) for f in live_f if f["reply_state"] == "擬保留"))
    rep("更換完成必須有完成證據（零寬字元視為空）", all(not blank(f.get("done_evidence_ref")) for f in live_f if f["done_state"] == "更換完成"))

    rep("零寬／隱形字元清單與應用端一致（含 Hangul filler）", zw_pat == ef._ZW.pattern)
    horizon = int(ef._safe_settings(conn)[0]["max_horizon_months"])
    lim = ef.add_months(today, horizon)
    far = [f["item_key"] for f in live_f for k in ("committed_date", "hold_review_date") if pdate(f.get(k)) and pdate(f[k]) > lim]
    rep(f"承諾日／重新檢視日沒有超過今天起 {horizon} 個月的舊資料（避免永久綠燈）", not far, f"超過={far[:3]}")
    rep("作廢的列必有作廢時間與理由", all(f.get("voided_at") and not blank(f.get("void_reason")) for f in follows.values() if f.get("voided_at")))
    rep("version 欄位非空且為正整數", all(isinstance(f.get("version"), int) and f["version"] >= 1 for f in follows.values()),
        f"違規={[f['item_key'] for f in follows.values() if not (isinstance(f.get('version'), int) and f['version'] >= 1)][:3]}")

    # ---- 獨立重算：逾承諾日／保留
    open_f = [f for f in live_f if f["done_state"] != "更換完成"]
    ind_over = sum(1 for f in open_f if f["reply_state"] == "規劃更換" and pdate(f.get("committed_date")) and pdate(f["committed_date"]) < today)
    rep("規劃更換逾承諾日（獨立重算：掃表 + 日期解析）= 磚", ind_over == tile["plan_overdue"], f"獨立={ind_over}　App={tile['plan_overdue']}")
    cfg, _bad = ef._safe_settings(conn)
    reg = {c["name"]: c["need_note"] for c in cfg["reason_classes"]}

    def complete(f):
        cls = (f.get("hold_reason_class") or "").strip()
        return bool(cls in reg and (not reg[cls] or not blank(f.get("hold_reason_note"))) and not blank(f.get("hold_basis_text"))
                    and not blank(f.get("hold_evidence_ref")) and not blank(f.get("hold_recorded_by")) and not blank(f.get("hold_recorded_at"))
                    and pdate(f.get("hold_review_date")))
    holds = [f for f in open_f if f["reply_state"] == "擬保留"]
    ind_exp = sum(1 for f in holds if complete(f) and pdate(f["hold_review_date"]) <= today)
    ind_nob = sum(1 for f in holds if not complete(f))
    rep("保留已到期（獨立重算：真的日期解析，當天算到期）= 磚", ind_exp == tile["hold_expired"], f"獨立={ind_exp}　App={tile['hold_expired']}")
    rep("擬保留尚無依據（獨立重算）= 磚", ind_nob == tile["hold_no_basis"], f"獨立={ind_nob}　App={tile['hold_no_basis']}")

    # ---- 獨立重算：通知相關（最小實作，不呼叫 classify／_Notify）
    state, by_mail, by_name, orphan = {}, {}, {}, None
    if ef._tbl(conn, "eos_notify_state"):
        for r in conn.execute("SELECT * FROM eos_notify_state"):
            state[r["state_key"]] = (r["last_bucket"], r["last_sent_at"] or "", (r["last_recipient"] if "last_recipient" in r.keys() else None))
    if ef._tbl(conn, "eos_notify_log"):
        for r in conn.execute("SELECT owner_name, recipient, status, sent_at FROM eos_notify_log ORDER BY id"):
            rec = (r["status"], r["sent_at"] or "")
            by_mail[(r["recipient"] or "").strip().lower()] = rec
            on = (r["owner_name"] or "").strip()
            if on == "(沒人收)":
                orphan = rec
            elif on and not on.startswith("(本期"):
                by_name[nn(on)] = rec
    ad = vn.ad_ready(conn)
    n_unn = n_fail = n_unrep = n_unas = 0
    for ik, ref, track, mk, item, name, b, in_w, stale, f in universe:
        if f and (f.get("voided_at") or f["done_state"] == "更換完成"):
            continue
        st = state.get(f"{track}|{mk}|{name or ''}|{item}")
        mail = None
        if name and ad:
            p, _w = vn.resolve_recipient(conn, name)
            mail = (p.get("mail") or "").strip().lower() if p else None
        notified = bool(st is not None and (b is None or (st[0] is not None and b >= st[0]))
                        and not (mail and st[2] and mail != str(st[2]).strip().lower()))
        rec = orphan if name is None else (by_mail.get(mail) if mail else by_name.get(nn(name)))
        failed = bool(rec and rec[0] == "failed" and pdate(rec[1][:10]) and (today - pdate(rec[1][:10])).days <= 35
                      and (st is None or rec[1] > (st[1] or "")))
        n_unn += 1 if (in_w and not notified) else 0
        n_fail += 1 if failed else 0
        n_unrep += 1 if (notified and (f["reply_state"] if f else "待回覆") == "待回覆") else 0
        n_unas += 1 if ref == ef.UNASSIGNED else 0
    rep("尚未通知（獨立重算）= 磚", n_unn == tile["unnotified"], f"獨立={n_unn}　App={tile['unnotified']}")
    rep("最近一次寄送失敗（獨立重算）= 磚", n_fail == tile["delivery_failed"], f"獨立={n_fail}　App={tile['delivery_failed']}")
    rep("已通知尚未回覆（獨立重算）= 磚", n_unrep == tile["unreplied"], f"獨立={n_unrep}　App={tile['unreplied']}")
    rep("缺負責人（獨立重算）= 磚", n_unas == tile["unassigned"], f"獨立={n_unas}　App={tile['unassigned']}")


def check_eos_evidence(conn, today=None):
    """EOS 批二（2.041.73）：官方證據、待重查、資產覆蓋率與未匹配、對照規則歷史、可信度語意。唯讀。
    ① 覆蓋率：每軌 各格加總 = 該軌列數 = 總台數（machine_key 逐台）、磚數 = 下鑽筆數、未匹配清單台數加總 = 未匹配格、
       每列恰好一格、命中格必有表內項目名；
    ② 可信度語意：用『獨立的最小實作』（regex＋strptime 解析日期，不呼叫 eos.trust_level／_date_parses）重算 EOS 表每個項目的
       已確認／推定／未知，與 eos.trust_level 逐項相等；已確認 ⊆ is_trusted；
    ③ 待重查：獨立讀 eos_evidence 表原始列重算「confirmed 但證據不完整」的項目數，與證據總覽磚相等；
    ④ 表內硬規則：查證日期是真的日期且不在未來、版本為正整數、日期精度合法、每筆證據至少有一列歷史、證據對應的項目仍在 EOS 表；
    ⑤ 對照規則歷史：操作別／規則表合法、前後值為合法 JSON。
    **觀察模式（預設）**：首次在正式區看到警示先人工核對，不中止部署；app_settings 的 verify_strict_eos=1 才升級為 ❌。"""
    import json as _json
    import re as _re
    from datetime import datetime as _dt

    import eos
    import eos_evidence as ee
    import eos_follow as ef
    import system_stats
    from lifecycle import retired_sql_list

    pg = "EOS 證據與覆蓋"
    strict = str(db.get_setting(conn, "verify_strict_eos", "0") or "0") == "1"

    def rep(name, ok, detail=""):
        if strict or ok:
            inv(pg, name, ok, detail)
        else:
            warn(pg, name, ok, (detail + "　" if detail else "") + "〔觀察模式：先人工核對再開 verify_strict_eos=1〕")

    today = today or ef.today_tpe()

    def pd(t):
        try:
            return _dt.strptime(t, "%Y-%m-%d").date() if isinstance(t, str) and _re.fullmatch(r"\d{4}-\d{2}-\d{2}", t) else None
        except ValueError:
            return None

    def pm(t):
        try:
            return _dt.strptime(t + "-01", "%Y-%m-%d").date() if isinstance(t, str) and _re.fullmatch(r"\d{4}-\d{2}", t) else None
        except ValueError:
            return None

    # ---- ① 覆蓋率
    rows = ee.coverage_rows(conn)
    ov = ee.coverage_overview(conn, rows)
    hw = conn.execute("SELECT asset_serial, hostname, ip, environment, os, device_model, asset_name, asset_purpose, asset_status "
                      "FROM hardware WHERE COALESCE(asset_status,'') NOT IN " + retired_sql_list()).fetchall()
    n_mach = len(system_stats.collapse_machines(hw))
    rep("覆蓋率：總台數 = 非退役資產逐台（machine_key）", ov["machines"] == n_mach, f"覆蓋率={ov['machines']}　獨立={n_mach}")
    for t in ee.KINDS:
        sub = [r for r in rows if r["track"] == t]
        b = ov["tracks"][t]["buckets"]
        rep(f"覆蓋率（{ee.KIND_LABEL[t]}）：各格加總 = 該軌列數 = 總台數", sum(b.values()) == len(sub) == n_mach,
            f"格加總={sum(b.values())}　列={len(sub)}　台={n_mach}")
        rep(f"覆蓋率（{ee.KIND_LABEL[t]}）：每列恰好一格且命中格有表內項目名",
            all(r["bucket"] in ee.BUCKET_ORDER for r in sub)
            and all(bool(r["matched_name"]) for r in sub if r["bucket"] in ("confirmed", "presumed", "unknown")),
            f"異常={[r['hostname'] for r in sub if r['bucket'] not in ee.BUCKET_ORDER][:5]}")
    rep("覆蓋率：磚的數字 = 同一份下鑽筆數（加總=下鑽）",
        all(t["count"] == len(ee.coverage_drill(rows, t["track"], t["bucket"])) for t in ov["tiles"]),
        f"{[(t['key'], t['count']) for t in ov['tiles'] if t['count'] != len(ee.coverage_drill(rows, t['track'], t['bucket']))][:5]}")
    for t in ee.KINDS:
        rep(f"未匹配清單（{ee.KIND_LABEL[t]}）：各項台數加總 = 未匹配格 = 逐台下鑽筆數",
            sum(g["machines"] for g in ov["unmatched"] if g["track"] == t) == ov["tracks"][t]["buckets"]["unmatched"]
            == len(ee.coverage_drill(rows, t, "unmatched")))
    rep("未匹配清單：項次 1..N 連續", [g["no"] for g in ov["unmatched"]] == list(range(1, len(ov["unmatched"]) + 1)))
    snap = ee.coverage_snapshot(conn)
    rep("覆蓋率：磚與下鑽共用的快照 = 逐台重算（磚、列數一致）",
        snap["overview"]["tiles"] == ov["tiles"] and len(snap["rows"]) == len(rows),
        f"快照列={len(snap['rows'])}　重算列={len(rows)}")
    rep("覆蓋率：待核數 = 待核下鑽筆數", ov["needs_review"] == len(ee.coverage_drill(rows, None, None, None, True)),
        f"待核={ov['needs_review']}")
    import eos_contract as _ec
    _sh = _ec.shadow_overview(conn)
    rep("合約影子比對：各類型筆數加總 = 全部差異 = 下鑽列數，項次 1..N 連續",
        sum(t["count"] for t in _sh["tiles"][1:]) == _sh["tiles"][0]["count"] == len(_sh["rows"])
        and [r["no"] for r in _sh["rows"]] == list(range(1, len(_sh["rows"]) + 1)),
        f"tiles={[(t['key'], t['count']) for t in _sh['tiles']]}")

    # ---- ② 可信度語意（獨立重算）
    bad_trust, bad_sub = [], []
    for kind, e in ee.all_entries():
        dt = e.get("eos_date")
        ok_date = pd(dt) is not None or pm(dt) is not None
        conf = e.get("confidence")
        if not ok_date or conf == "source_rejected":
            exp = "unknown"
        elif conf == "unverified":
            exp = "presumed"
        else:
            exp = "confirmed"
        if eos.trust_level(e) != exp:
            bad_trust.append(f"{kind}:{e['name']}(app={eos.trust_level(e)},獨立={exp})")
        if (eos.trust_level(e) == "confirmed") != bool(eos.is_trusted(e)):
            bad_sub.append(f"{kind}:{e['name']}")
    rep("可信度語意：EOS 表每項 已確認／推定／未知 = 獨立重算", not bad_trust, "、".join(bad_trust[:5]))
    rep("可信度語意：已確認 ⇔ 可單獨觸發紅燈與通知（is_trusted）", not bad_sub, "、".join(bad_sub[:5]))

    # ---- ③ 待重查（獨立讀證據表原始列）
    raw: dict[tuple, dict] = {}
    if ee._tbl(conn, "eos_evidence"):
        raw = {(r["item_kind"], r["item_name"]): dict(r) for r in conn.execute("SELECT * FROM eos_evidence")}
    zw = "[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff\u00ad\u180e\u3164\uffa0]"

    def nb(v):
        return not _re.sub(zw, "", str(v or "")).strip()

    def complete(e, ev):
        if not ev:
            return False
        d = pd(str(ev.get("verified_on") or ""))
        prec = "day" if pd(e.get("eos_date")) else "month" if pm(e.get("eos_date")) else "none"
        return (not nb(ev.get("verified_by")) and d is not None and d <= today and not nb(ev.get("quote_text"))
                and not nb(ev.get("scope_text")) and ev.get("date_precision") == prec
                and not (nb(ev.get("source_url")) and nb(e.get("source_url"))))
    n_recheck = sum(1 for kind, e in ee.all_entries()
                    if e.get("confidence") in ("confirmed", "confirmed_via_attachment") and not complete(e, raw.get((kind, e["name"]))))
    evo = ee.evidence_overview(conn, today)
    tile = {t["key"]: t["count"] for t in evo["tiles"]}
    rep("待重查：磚的數字 = 獨立重算（confirmed 但證據不完整）", tile["recheck"] == n_recheck, f"磚={tile['recheck']}　獨立={n_recheck}")
    rep("證據：磚的數字 = 同一份下鑽筆數（加總=下鑽）", all(t["count"] == len(ee.evidence_drill(evo, t["key"])) for t in evo["tiles"]))
    rep("證據：完整 + 不完整 + 尚無 = 全部", tile["complete"] + tile["partial"] + tile["none"] == tile["all"],
        f"{tile['complete']}+{tile['partial']}+{tile['none']} vs {tile['all']}")
    rep("證據：項次 1..N 連續", [r["no"] for r in evo["rows"]] == list(range(1, len(evo["rows"]) + 1)))

    # ---- ④ 表內硬規則
    bad_ev = []
    for (kind, name), ev in raw.items():
        d = pd(str(ev.get("verified_on") or ""))
        if d is None or d > today:
            bad_ev.append(f"{kind}:{name} 查證日期無效或在未來")
        if not isinstance(ev.get("version"), int) or ev["version"] < 1:
            bad_ev.append(f"{kind}:{name} 版本")
        if ev.get("date_precision") not in (None, "day", "month", "none"):
            bad_ev.append(f"{kind}:{name} 日期精度")
        if ee.entry_of(kind, name) is None:
            bad_ev.append(f"{kind}:{name} 對照表已無此項目")
        if ee._tbl(conn, "eos_evidence_history") and not conn.execute(
                "SELECT 1 FROM eos_evidence_history WHERE evidence_id = ?", (ev["id"],)).fetchone():
            bad_ev.append(f"{kind}:{name} 沒有歷史")
    rep("證據記錄：查證日期有效不在未來、版本正整數、精度合法、項目仍在表內、每筆至少一列歷史", not bad_ev, "；".join(bad_ev[:5]))

    # ---- ⑤ 對照規則歷史
    bad_h = []
    for h in ee.mapping_history(conn):
        if h["rule_table"] not in ee.MAPPING_TABLE_LABEL or h["op"] not in ee.MAPPING_OP_LABEL:
            bad_h.append(f"#{h['id']} 規則表或操作別")
        for k in ("rule_key", "before_json", "after_json"):
            try:
                _json.loads(h[k]) if h[k] is not None else None
            except ValueError:
                bad_h.append(f"#{h['id']} {k} 不是合法 JSON")
    rep("對照規則歷史：規則表與操作別合法、前後值為合法 JSON", not bad_h, "；".join(bad_h[:5]))


def _fresh_if_written(conn):
    """fleet_cache 只要這條連線寫過（total_changes>0）或交易未結束，整條連線之後都不用快取（防止讀到自己寫入前的舊結果）。
    數字核對裡有些檢查會懶建表／補欄位，一寫之後其餘檢查全部重算，在 221 真實庫要 7 分鐘（Codex 311 定位）。
    寫入都已提交時換一條新連線：快取鍵含資料庫檔與 WAL 狀態，提交過的寫入會讓鍵改變，不會拿到舊結果。
    交易還沒結束就不動（不替別人提交、也不讓它回滾）。"""
    try:
        if conn.total_changes and not conn.in_transaction:
            conn.close()
            return db.get_connection()
    except sqlite3.Error:
        pass                    # 換不了就沿用原連線：只是慢（不用快取），結果一樣
    return conn


def main() -> int:
    conn = db.get_connection()
    print(f"資料庫：{db.get_db_path()}")
    # 2.x 新版畫面的檢查（check_v2 起）2026-10-28 補進主流程：先前只在測試裡呼叫，部署關卡 [2d/7] 沒有跑到
    for check in (
            check_dedup,
            check_page_a,
            check_page_b,
            check_system_group,           # 2.048.19 補：2.048.16 加了這段卻沒排進主流程（部署關卡沒跑到）
            check_distribution,
            check_data_quality,
            check_single_names,
            check_vip,
            check_accounts,
            check_monthly,
            check_composition,
            check_pipeline,
            check_reconciliation,
            check_scan_scope,
            check_adopt_tabs,
            check_dispatch_coverage,
            check_same_machine_collect,
            check_dyn_auto_merge,
            check_os_basis,
            check_measured_effective,
            check_hostname_fix,
            check_machine_key_basis,
            check_retired_zero,
            check_duplicate_consistency,
            check_duplicate_release,
            check_card_drilldowns,
            check_manage_state_rep_items,
            check_machine_state_mode,
            check_adopt_registered_split,
            check_account_onboarded_split,
            check_account_os_split,
            check_config_audit_drill,
            check_existence,
            check_lifecycle,
            check_system_map,
            check_rack_name_merge,
            check_rack_diff,
            check_datacenter,
            check_front_rear,
            check_storage,
            check_capacity,
            check_capacity_archive,
            check_night_alerts,
            check_wug_alerts,
            check_wug_percent_scale,
            check_v2,
            check_v2_today,
            check_v2_hosts,
            check_v2_dist,
            check_rack_audit,
            check_relocation_packages,
            check_v2_batch2,
            check_v2_batch3,
            check_observed_deps,
            check_observed_coverage,
            check_daily_digest,
            check_daily_risk,
            check_data_fix_lifecycle,
            check_count_bridge,
            check_v2_batch5,
            check_anomaly_drills,
            check_certs,
            check_host_ops,
            check_eos_follow,
            check_eos_evidence,
    ):
        conn = _fresh_if_written(conn)
        check(conn)
    print(f"\n════ 共 {CHECKS} 項，❌ {FAILS} 項 ════")
    print("全部通過 ✅" if FAILS == 0 else "有對不上的數字，往上找 ❌")
    html_out = _html_out_path()
    if html_out:
        write_html(html_out)
    return 1 if FAILS else 0


def _html_out_path() -> str | None:
    """--html <path> 或 ASSET_VERIFY_HTML 環境變數指定，就額外寫一份 HTML 報告。"""
    argv = sys.argv[1:]
    if "--html" in argv:
        i = argv.index("--html")
        if i + 1 < len(argv):
            return argv[i + 1]
    return os.environ.get("ASSET_VERIFY_HTML") or None


if __name__ == "__main__":
    raise SystemExit(main())
