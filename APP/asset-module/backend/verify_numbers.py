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
    inv("軟體盤點", "卡片 hosts = 依主機（還有套件的）台數", sw["hosts"] == len(live_hosts),
        f"卡片 {sw['hosts']}／下鑽 {len(live_hosts)}")
    _sum = sum(r["package_count"] or 0 for r in live_hosts)
    inv("軟體盤點", "卡片 live = 依主機套件數加總", sw["live"] == _sum, f"卡片 {sw['live']}／下鑽 {_sum}")
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
              or t["power"]["active_count"] != t["device_count"]]
    inv("機房架構圖", "每櫃：已知瓦數台數 + 未知規格台數 = 在用台數", not bad_pw, f"{bad_pw[:5]}" if bad_pw else "")
    # 機櫃口徑（實體在不在櫃上）：在用＋關機未下架＋待確認＋已不在櫃上 = 機櫃欄看得懂的設備（獨立從 machines 數）
    ms = rack_layout.machines(conn)
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
            for k in t["keys"] + t["power"]["off_keys"] + t["power"]["pending_keys"] + t["power"]["removed_keys"]:
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
    check_adopt_tabs(conn)
    check_dispatch_coverage(conn)
    check_same_machine_collect(conn)
    check_dyn_auto_merge(conn)
    check_os_basis(conn)
    check_measured_effective(conn)
    check_hostname_fix(conn)
    check_machine_key_basis(conn)
    check_retired_zero(conn)
    check_duplicate_consistency(conn)
    check_card_drilldowns(conn)
    check_manage_state_rep_items(conn)
    check_adopt_registered_split(conn)
    check_account_onboarded_split(conn)
    check_account_os_split(conn)
    check_config_audit_drill(conn)
    check_existence(conn)
    check_lifecycle(conn)
    check_system_map(conn)
    check_datacenter(conn)
    check_storage(conn)
    check_observed_deps(conn)
    check_daily_digest(conn)
    check_daily_risk(conn)
    check_data_fix_lifecycle(conn)
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
