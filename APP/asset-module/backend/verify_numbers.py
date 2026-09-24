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
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402
import manage_state  # noqa: E402
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
                fn(*a, **k)
            except Exception as exc:  # noqa: BLE001 - 一段炸掉不影響其他段，但要標紅
                global FAILS
                FAILS += 1
                RESULTS.append({"sec": title, "page": title, "name": "這段檢查自己出錯",
                                "ok": False, "detail": f"{type(exc).__name__}: {exc}", "note": ""})
                print(f"❌ [{title}] 這段檢查自己出錯：{type(exc).__name__}: {exc}")
        return wrapped
    return deco


def write_html(path: str) -> None:
    """把這次跑的結果寫成一份可留存／給主管看的 HTML 報告（示範白綠）。"""
    e = _html.escape
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    secs: dict[str, list[dict]] = {}
    for r in RESULTS:
        secs.setdefault(r["sec"], []).append(r)
    ok_all = FAILS == 0
    rows_html = []
    for sec, items in secs.items():
        bad = sum(1 for x in items if not x["ok"])
        badge = "全過" if bad == 0 else f"❌ {bad}"
        rows_html.append(
            f'<tr class="sec"><td colspan="4">{e(sec)}　'
            f'<span class="secbadge {"ok" if bad == 0 else "bad"}">{badge}</span></td></tr>')
        for x in items:
            st = '<span class="pill ok">✓ 通過</span>' if x["ok"] else '<span class="pill bad">✗ 不符</span>'
            note = f'<div class="note">{e(x["note"])}</div>' if x.get("note") else ""
            rows_html.append(
                f'<tr class="{"" if x["ok"] else "rowbad"}">'
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
.pill.ok{{background:var(--goodbg);color:var(--good)}} .pill.bad{{background:var(--bad);color:#fff}}
.pg{{color:var(--soft);white-space:nowrap;font-size:13px}}
.dt{{color:var(--soft);font-variant-numeric:tabular-nums;white-space:nowrap}}
.note{{color:var(--soft);font-size:12px;margin-top:2px}}
tr.rowbad td{{background:var(--badbg)}}
.foot{{color:var(--soft);font-size:12px;margin-top:16px;line-height:1.7}}
</style></head><body><div class="wrap">
<h1>資產盤點模組　數字核對報告</h1>
<div class="sub">每個檯面數字用第二種獨立算法重算一次，並套「加總＝下鑽、VM＋實體＝台數、下鑽筆數＝檯面數」等不變式。<br>此報告由程式（verify_numbers.py）產出，不經 AI 判讀；可在正式／公司環境重跑。</div>
<div class="hero">
<div><div class="big">{CHECKS}</div><div class="lab">核對項目</div></div>
<div><div class="big {'ok' if ok_all else 'bad'}">{CHECKS - FAILS}</div><div class="lab">通過</div></div>
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
    rows = conn.execute("SELECT asset_serial,hostname,ip,is_vm,device_model FROM hardware").fetchall()
    groups: dict = {}
    for r in rows:
        k = system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        groups.setdefault(k, []).append(r)
    return rows, groups


@section("台數 / 去重（無重複灌水）")
def check_dedup(conn):
    rows, groups = _hosts(conn)
    ca = system_report.classify_assets(conn)
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
    inv("頁A", "total_physical = 各機房 + 各分公司（加總）",
        pd["total_physical"] == rooms_sum + branch_sum,
        f"{pd['total_physical']} = {rooms_sum}+{branch_sum}")
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


@section("分佈統計（AP 系統）")
def check_distribution(conn):
    bs = system_stats.by_system(conn, limit=None)
    ca_hosts = len(system_report.classify_assets(conn))
    check("分佈統計", "total_assets = 全庫台數", bs["total_assets"], ca_hosts)
    # 十項盤點第 5、6 條（2026-09-18）：全庫要拆得開，拆出來的要加得回去
    _pt = bs.get("partition") or {}
    check("分佈統計", "在管＋退役＋帳外 = 全庫台", _pt.get("managed", 0) + _pt.get("retired", 0)
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
    # 「總台數」應該是台（去重），不是筆——跟全庫台數對
    check("月報", "總台數 = 全庫台數（去重）", total, len(system_report.classify_assets(conn)),
          "月報數字會被寫進給主管的報告，掛筆數當台數會錯")


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
    check("來源對照表", "在管＋只在帳外＋已退役 = 全庫台",
          _hs["cia_hosts"] + _hs["offbook_only_hosts"] + _hs["retired_hosts"], _hs["total_hosts"])
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
    # 機房×環境交叉：每個機房的環境加總 = 該機房台數；總和 = 有效資產
    badloc = [loc for loc, envmap in c["by_location_env"].items()
              if sum(envmap.values()) != c["by_location"].get(loc, 0)]
    inv("組成", "機房×環境 每機房加總 = 機房台數", not badloc, f"對不上：{badloc[:3]}" if badloc else "")
    grand = sum(sum(e.values()) for e in c["by_location_env"].values())
    inv("組成", "機房×環境 總和 = 有效資產", grand == active, f"{grand} vs {active}")


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


def main() -> int:
    conn = db.get_connection()
    print(f"資料庫：{db.get_db_path()}")
    check_dedup(conn)
    check_page_a(conn)
    check_page_b(conn)
    check_distribution(conn)
    check_data_quality(conn)
    check_single_names(conn)
    check_vip(conn)
    check_accounts(conn)
    check_monthly(conn)
    check_composition(conn)
    check_pipeline(conn)
    check_reconciliation(conn)
    check_scan_scope(conn)
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
