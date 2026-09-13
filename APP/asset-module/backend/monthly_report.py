"""月報匯出：把 raw data ＋提示詞合成一個檔，人拿去給核准的 GPT 打「請分析」。

## 這支不做分析

決策（2026-09-07 拍板）：**系統只匯出，分析交給外部 GPT**。Top risk、磁碟幾個月後會滿、
跟上月比是好是壞——全部由 GPT 從這份檔案推。這裡是純程式，沒有任何 AI 呼叫。

理由：分析要的是判斷力與跨領域常識，寫死規則做不到；而把 LLM 接進這套系統
會多一條對外連線與一個資安關卡。匯出一個檔給人自己貼，成本最低、責任最清楚。

## 最重要的一件事：**不准 GPT 腦補**

Performance 與 Capacity 是月報兩大構面，而 `host_metric_latest` **現在是空的**
（push agent 還沒回報 CPU／磁碟）。如果檔案裡那兩段留白，GPT 會自己編一個看起來
合理的趨勢——而部主管會拿那個去做決策。

所以：
  1. 每個構面都明寫「有資料／無資料」與來源，不留白
  2. 提示詞裡下死命令：沒有資料的直接寫「本月無資料」，不准推測
  3. 檔案開頭放一張涵蓋表，讓人（跟 GPT）先知道這份能回答到什麼程度

「沒查」跟「查了沒問題」是兩件事——這條在這裡尤其致命，因為輸出會被當成給主管的報告。

## 顆粒度：彙總 ＋ 有問題的才列明細

決策走 C（只給核准的企業版／地端 GPT 才放明細），但「放明細」不等於「全倒」——
3,600 多台逐台倒進去，GPT 的 context 根本吃不下。所以：正常的只給數字，
**異常的逐筆列出**（過期的、有稽核發現的、連不到的）。GPT 要答「最擔心的三件事」
需要的正是那些異常，不是那三千台正常機器。

## 每月凍結一份快照

決策：同月重跑**覆蓋**（使用者 2026-09-08 選 A），但記下每次的產生時間與產生者。
月報是當月結算，中途重產是正常的（資料還在補）；凍結的意義是「跨月比較有一致基準」，
不是「當月不能改」。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

#: 六個管理構面（月報格式要求）。這裡只做「主機」欄——資料庫欄是 DBA 組、
#: 網路/資安欄是網路組，決策 2026-09-07：先不做。
CONSTRUCTS = ["Availability", "Performance", "Capacity",
              "Lifecycle", "Security & Compliance", "Resilience"]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _one(conn, sql: str, params: tuple = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# 各構面：每一段都自己回報「有沒有資料」，不靠呼叫端猜
# ---------------------------------------------------------------------------

#: 外部來源：這些構面的資料在既有監控工具裡，本系統**刻意不重複收集**。
#:
#: 使用者 2026-09-08：「這三個我們可以先不做，以後可以去撈 What'sUp API，欄位先空出來。」
#: 而且這跟這套系統存在的理由一致——資安申請書寫的是「現有 What'sUp／vCenter／
#: OPManager 主要提供效能與可用性監控，**無法**完整提供資產盤點、帳號生命週期、
#: 服務拓樸」。我們再去收 CPU 是重複造輪子，也跟自己講過的話矛盾。
EXTERNAL_SOURCE = "What'sUp Gold／OPManager／vCenter（監控組）"


def _section(name: str, construct: str, source: str, external: bool = False):
    """裝飾器：把一段資料收集包成統一形狀，並且**失敗要說出來**。

    一段炸掉不該讓整份月報產不出來——但也不能安靜跳過。回 `error` 讓涵蓋表
    寫成「取得失敗：⋯」，人看得出這份報告缺了哪一塊、為什麼。
    """
    def deco(fn):
        def wrapper(conn):
            try:
                data = fn(conn)
                has = bool(data.get("_has_data", True))
                data.pop("_has_data", None)
                return {"name": name, "construct": construct, "source": source,
                        "external": external,
                        "has_data": has, "error": None, "data": data}
            except Exception as exc:  # noqa: BLE001
                return {"name": name, "construct": construct, "source": source,
                        "external": external,
                        "has_data": False, "error": f"{type(exc).__name__}: {exc}",
                        "data": {}}
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


@_section("資產總覽", "—", "hardware 表")
def _assets(conn) -> dict:
    total = _one(conn, "SELECT COUNT(*) FROM hardware") or 0
    by_env = {r[0] or "(未填)": r[1] for r in conn.execute(
        "SELECT environment, COUNT(*) FROM hardware GROUP BY 1 ORDER BY 2 DESC")}
    by_kind = {("VM" if r[0] == 1 else "實體/未標"): r[1] for r in conn.execute(
        "SELECT is_vm, COUNT(*) FROM hardware GROUP BY 1")}
    return {"總台數": total, "依環境": by_env, "虛實": by_kind, "_has_data": total > 0}


@_section("納管涵蓋率（不是可用率）", "Availability（僅涵蓋率，無 uptime）",
          "hardware.collect_ok、host_service")
def _availability(conn) -> dict:
    ok = _one(conn, "SELECT COUNT(*) FROM hardware WHERE collect_ok = 1") or 0
    failed = _one(conn, "SELECT COUNT(*) FROM hardware WHERE collect_ok = 0 "
                        "AND COALESCE(collect_error,'') NOT LIKE '%取消納管%'") or 0
    revoked = _one(conn, "SELECT COUNT(*) FROM hardware WHERE collect_ok = 0 "
                         "AND COALESCE(collect_error,'') LIKE '%取消納管%'") or 0
    never = _one(conn, "SELECT COUNT(*) FROM hardware WHERE collect_ok IS NULL") or 0
    svc = _one(conn, "SELECT COUNT(*) FROM host_service WHERE gone_at IS NULL") or 0
    # 連不上的逐台列出——那是要人去處理的，數字本身不夠用
    down = [dict(r) for r in conn.execute(
        "SELECT asset_serial, hostname, ip, environment, collect_error, collect_checked_at "
        "FROM hardware WHERE collect_ok = 0 "
        "AND COALESCE(collect_error,'') NOT LIKE '%取消納管%' "
        "ORDER BY environment, ip LIMIT 200")]
    return {
        "收得到": ok, "連不上": failed, "已取消納管": revoked, "從未試連": never,
        "採集到的服務筆數": svc,
        "連不上的主機（最多列 200 台，多半是尚未納管而非故障）": down,
        "⚠️ 這一段最容易被誤讀，請照這樣理解": (
            "「連不上」的主因是**還沒佈收集帳號**（批次納管尚未完成），"
            "不是主機故障、不是服務中斷。"
            "\n\n"
            "❌ 不可以寫成：可用率 {pct}%、系統大規模離線、重大可用性風險。\n"
            "✅ 應該寫成：納管涵蓋率 {pct}%，其餘主機尚未佈署收集帳號，"
            "因此本月**沒有**可用率資料。"
            "\n\n"
            "真正的 Availability（uptime%／事故次數／MTTR）本系統沒有量測，"
            "也沒有事故表——那一段請直接寫「本月無資料」。"
        ).format(pct=round(ok / max(ok + failed + never, 1) * 100, 1)),
        "納管涵蓋率(%)": round(ok / max(ok + failed + never, 1) * 100, 1),
        # 「有沒有納管涵蓋率的資料」是 True；但 Availability 本身是 False。
        # 兩件事不同，涵蓋表裡要看得出來。
        "_has_data": (ok + failed + never) > 0,
    }


@_section("效能", "Performance", EXTERNAL_SOURCE, external=True)
def _performance(conn) -> dict:
    n = _one(conn, "SELECT COUNT(*) FROM host_metric_latest") or 0
    keys = [r[0] for r in conn.execute(
        "SELECT DISTINCT metric_key FROM host_metric_latest LIMIT 20")]
    return {
        # 欄位先空出來（使用者 2026-09-08）：之後接 What'sUp API 填進來
        "CPU 使用率": None, "記憶體使用率": None, "趨勢": None,
        "資料來源": EXTERNAL_SOURCE,
        "狀態": "尚未介接",
        "本系統目前的 metric 筆數（僅供參考，非月報用）": n,
        "⚠️ 請這樣寫": (
            "效能數據由既有監控工具提供，本系統**不重複收集**。"
            "\n\n"
            "❌ 不要寫成：本月無效能資料／無法評估效能風險。\n"
            "✅ 請寫成：CPU／記憶體趨勢由監控組提供，本月尚未取得。"
        ),
        "_has_data": False,
    }


@_section("容量", "Capacity", EXTERNAL_SOURCE, external=True)
def _capacity(conn) -> dict:
    segs = _one(conn, "SELECT COUNT(*) FROM network_segment") or 0
    disk = _one(conn, "SELECT COUNT(*) FROM host_metric_latest "
                      "WHERE metric_key LIKE '%disk%'") or 0
    return {
        # 欄位先空出來（使用者 2026-09-08）：之後接 What'sUp API 填進來
        "運算容量": None, "儲存容量": None, "磁碟成長趨勢": None,
        "資料來源": EXTERNAL_SOURCE,
        "狀態": "尚未介接",
        # 這個是我們自己的資料，跟上面三個不同：IP 位址容量本系統有
        "IP 網段筆數（本系統有，屬位址容量不是運算/儲存容量）": segs,
        "⚠️ 請這樣寫": (
            "運算與儲存容量由既有監控工具提供，本系統**不重複收集**。"
            "IP 網段容量是本系統的資料，但那是位址容量，不能拿來回答"
            "「磁碟幾個月後會滿」。"
            "\n\n"
            "❌ 不要寫成：本月無容量資料／無法預測容量天花板。\n"
            "✅ 請寫成：運算／儲存容量由監控組提供，本月尚未取得。"
        ),
        "_has_data": False,
    }


@_section("生命週期 / EOS", "Lifecycle", "eos 對照表 ＋ hardware.os／device_model")
def _lifecycle(conn) -> dict:
    import eos
    import normalize

    expired, upcoming = [], []
    for r in conn.execute(
            "SELECT asset_serial, hostname, ip, environment, os, device_model, "
            "       asset_purpose, usage_unit "
            "FROM hardware WHERE COALESCE(asset_status,'') NOT IN "
            "('停用','報廢','閒置') ORDER BY environment, hostname"):
        hw = dict(r)
        for kind, val in (("OS", hw.get("os")), ("硬體", hw.get("device_model"))):
            if not val:
                continue
            if kind == "OS":
                info = normalize.normalize_os(val, conn, hw.get("device_model"))
                hit = eos.lookup_os_eos(info["canonical"])
            else:
                info = normalize.normalize_model(val, conn)
                hit = eos.lookup_hardware_eos(info["canonical"])
            if not hit:
                continue
            status = eos.eos_status(hit.get("eos_date"))
            item = {"資產序號": hw["asset_serial"], "主機名": hw.get("hostname"),
                    "環境": hw.get("environment"), "使用單位": hw.get("usage_unit"),
                    "類別": kind, "項目": info["canonical"],
                    "EOS 日期": hit.get("eos_date")}
            if status == "expired":
                expired.append(item)
            elif status == "upcoming":
                upcoming.append(item)
    return {
        "已過 EOS": len(expired),
        "一年內到期": len(upcoming),
        "已過 EOS 明細": expired[:300],
        "一年內到期明細": upcoming[:300],
        "⚠️ 說明": "查不到官方 EOS 日期的項目不列入上面兩個數字——那是「不知道」不是「沒問題」。",
        "_has_data": bool(expired or upcoming),
    }


@_section("帳號稽核 / 合規", "Security & Compliance", "account_finding、host_account")
def _security(conn) -> dict:
    accounts = _one(conn, "SELECT COUNT(*) FROM host_account WHERE gone_at IS NULL") or 0
    hosts = _one(conn, "SELECT COUNT(DISTINCT ip) FROM host_account "
                       "WHERE gone_at IS NULL") or 0
    by_verdict: dict[str, int] = {}
    findings: list[dict] = []
    last_run_at = None
    try:
        # 只算最近一次成功的盤點：account_finding 每輪都新增一批、舊的留著對照，
        # 不限 run 的話每多收一輪數字就膨脹一次（2026-09-11 221 實測 93 條被報成 186）。
        # 跟帳號頁 latest_findings / audit_summary 同一個口徑。
        last = conn.execute(
            "SELECT id, started_at FROM account_collect_runs WHERE status = 'ok' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        if last:
            last_run_at = last[1]
            for r in conn.execute(
                    "SELECT verdict, COUNT(*) FROM account_finding WHERE run_id = ? "
                    "GROUP BY 1", (last[0],)):
                by_verdict[r[0] or "(未分類)"] = r[1]
            findings = [dict(r) for r in conn.execute(
                "SELECT ip, username, rule_id, verdict, detail FROM account_finding "
                "WHERE run_id = ? AND verdict = 'fail' ORDER BY ip, username LIMIT 300",
                (last[0],))]
    except sqlite3.Error:
        pass
    return {
        "最新盤點時間": last_run_at or "（尚未盤點）",
        "已盤點帳號數": accounts, "涵蓋主機數": hosts,
        "稽核結果分佈": by_verdict,
        "未通過的發現（最多 300 筆）": findings,
        "⚠️ 說明": ("修補狀態、弱點掃描、攻擊統計**沒有資料**——那些要接 SOC 或"
                   "弱掃工具，本系統沒有。這一段只涵蓋帳號治理。"),
        "_has_data": accounts > 0,
    }


@_section("備份 / 復原", "Resilience", "backup 目錄與 app_settings")
def _resilience(conn) -> dict:
    info: dict[str, Any] = {}
    try:
        import backup
        d = backup.get_backup_dir()
        files = sorted(d.glob("*.db*")) if d.exists() else []
        info["備份檔數"] = len(files)
        info["最新備份"] = (
            datetime.fromtimestamp(files[-1].stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            if files else None)
        off = backup.get_offsite_dir(conn)
        info["異地備份"] = str(off) if off else "未設定"
    except Exception as exc:  # noqa: BLE001
        info["備份查詢失敗"] = str(exc)
    info["⚠️ 說明"] = ("這是**本系統自己**的資料庫備份。目標主機的 HA／DR／備份還原"
                      "狀態沒有資料——那要各系統管理者提供，本系統收不到。")
    info["_has_data"] = bool(info.get("備份檔數"))
    return info


@_section("資料品質", "—", "data_quality.measure")
def _quality(conn) -> dict:
    import data_quality
    m = data_quality.measure(conn)
    return {"量測": m,
            "⚠️ 說明": "月報的每個數字都建立在這份資料上。資料品質差，上面所有結論都要打折。",
            "_has_data": bool(m)}


@_section("可用率 / 事故", "Availability", EXTERNAL_SOURCE, external=True)
def _uptime(conn) -> dict:
    """獨立成一段，就為了在涵蓋表裡明白佔一行「無資料」。

    不這樣做的話，涵蓋表 Availability 那一格會被「納管涵蓋率」佔掉並標成
    ✅ 有資料——而那不是可用率。看表的人（跟 GPT）會以為可用率有量。
    """
    return {
        # 欄位先空出來：之後接 What'sUp API 只要把值填進來，消費端不用改
        "uptime%": None, "事故次數": None, "MTTR": None,
        "資料來源": EXTERNAL_SOURCE,
        "狀態": "尚未介接",
        "⚠️ 請這樣寫": (
            "本系統**刻意不收集**可用率與事故資料——那是既有監控工具的職責"
            "（現有 What'sUp Gold／vCenter／OPManager 就是做這個的）。"
            "\n\n"
            "❌ 不要寫成：本月無可用率資料／監控不足／建議補強監控。\n"
            "✅ 請寫成：可用率與事故統計由監控組提供，本月尚未取得，"
            "建議列入「需主管協助事項」。"
        ),
        "_has_data": False,
    }


SECTIONS = [_assets, _uptime, _availability, _performance, _capacity,
            _lifecycle, _security, _resilience, _quality]


# ---------------------------------------------------------------------------
# 組裝
# ---------------------------------------------------------------------------

def collect(conn) -> dict:
    """跑完所有段落。回 {"sections": [...], "generated_at": ...}。"""
    return {"generated_at": _now(),
            "sections": [fn(conn) for fn in SECTIONS]}


PROMPT = """\
你是這家金融機構資訊部門的維運分析助手。下面是本月的維運原始資料，
請據此寫一份**給部門主管看的月報**。

## 硬性規則（違反其中任何一條，這份報告就是不能用的）

1. **只能用檔案裡的資料。** 沒有出現在下面的數字一律不准出現在報告裡。
2. **標著「❌ 無資料」的段落，直接寫「本月無資料」並說明原因，不准推測、
   不准估算、不准用業界平均值代替。** 沒有量到的東西，任何趨勢或
   「幾個月後會滿」的推論都是編的。
3. **「沒查」跟「查了沒問題」要分開講。** 例如查不到 EOS 日期的項目是「不知道」，
   不可以寫成「沒有到期風險」。
4. **標「🔌 保留（外部來源）」的段落不是我們的缺口。** 效能、容量、可用率由既有監控
   工具（What'sUp Gold／vCenter／OPManager）負責，本系統刻意不重複收集。
   那些段落請寫「由監控組提供，本月尚未取得」並列入「需主管協助事項」，
   **不要**寫成「監控不足」或「建議補強監控」——那會把別組的工作寫成我們的缺失。
5. 每個結論後面標出它根據哪一段資料。
6. 用繁體中文，語氣平實，不要行銷詞。

## 報告結構（盡量 10 頁以內）

- **2.1 Executive Summary** — 本月整體燈號 ＋ Top 3~5 風險 ＋ 需要主管協助的事項
- **2.2 Service Health** — 可用性 / 事故
- **2.3 Performance & Capacity** — 趨勢，以及何時會觸及天花板
- **2.4 風險 & 合規** — EOS / 弱點 / 稽核
- **2.5 架構及復原機制** — HA / 備份 / DR
- **2.6 Action Tracking** — 上月風險改善進度
- **2.7 Next 3~6 Months** — 接下來 3~6 個月的重大變更與風險
- **2.8 Appendix** — 明細

## 報告必須答得出主管會問的五題

1. 這個月跟上個月比，風險是上升還是下降？
2. 目前最擔心的三件事是什麼？
3. 如果交易量突然變成現在的 1.5~2 倍，哪裡會先出問題？
4. 上個月答應改善的事，哪些完成？哪些 delay？為什麼？
5. 有哪件事是我們自己處理不了、需要主管出手的？

**答不出來的題目，就明白寫「這題目前答不出來，因為缺 ⋯⋯ 資料」——
不要為了讓報告完整而編一個答案。** 主管拿這份去做決策，編的比缺的傷害大。
"""


def render_markdown(conn, year_month: str, generated_by: str,
                    previous: dict | None = None) -> str:
    """產出要交給 GPT 的那一個檔。"""
    payload = collect(conn)
    lines: list[str] = []
    a = lines.append

    a(f"# 維運月報原始資料 — {year_month}")
    a("")
    a("> 🔒 **機密｜限內部使用。只能貼給公司核准的企業版／地端 GPT，"
      "不要貼到公開的 ChatGPT 或任何外部服務。**")
    a("> 本檔含主機名稱、位址與帳號明細。")
    a("")
    a(f"- 產生時間：{payload['generated_at']}")
    a(f"- 產生者：{generated_by}")
    a(f"- 涵蓋月份：{year_month}")
    a("- 產生方式：系統直接從資料庫匯出，**沒有經過任何 AI 加工**")
    a("")
    a("---")
    a("")
    a("## 這份資料能回答到什麼程度")
    a("")
    a("先看這張表再往下讀。三種狀態意思完全不同，**不要混為一談**：")
    a("")
    a("- ✅ **有** —— 本系統實際量到的")
    a("- 🔌 **保留（外部來源，未介接）** —— 資料在監控工具那邊，本系統刻意不重複收集。")
    a("  **這不是缺口，是分工。** 之後會接 API，欄位已經先留好")
    a("- ❌ **無資料** —— 真的沒量到。**不是沒問題，是沒量測**")
    a("")
    a("| 構面 | 段落 | 有真資料？ | 來源 |")
    a("|---|---|---|---|")
    for s in payload["sections"]:
        if s["has_data"]:
            mark = "✅ 有"
        elif s["error"]:
            mark = "⚠️ 取得失敗"
        elif s.get("external"):
            # 「保留待接」跟「無資料」是兩件事：前者是分工，後者才是缺口。
            # 混成一種，GPT 會把責任寫到我們頭上。
            mark = "🔌 保留（外部來源，未介接）"
        else:
            mark = "❌ 無資料"
        a(f"| {s['construct']} | {s['name']} | {mark} | {s['source']} |")
    a("")
    external = [s["name"] for s in payload["sections"] if s.get("external")]
    if external:
        a(f"**外部來源、本系統刻意不收的段落：{'、'.join(external)}。**")
        a(f"這些的資料在 {EXTERNAL_SOURCE}。**這不是我們的缺口，是分工**——")
        a("報告裡請寫「由監控組提供，本月尚未取得」，並列入「需主管協助事項」，")
        a("**不要**寫成「無資料」或「建議補強監控」。")
        a("")
    missing = [s["name"] for s in payload["sections"]
               if not s["has_data"] and not s["error"] and not s.get("external")]
    if missing:
        a(f"**本月完全沒有資料的段落：{'、'.join(missing)}。**"
          f"報告裡這些段落請直接寫「本月無資料」。")
        a("")
    broken = [f"{s['name']}（{s['error']}）" for s in payload["sections"] if s["error"]]
    if broken:
        a(f"**⚠️ 取得失敗的段落：{'；'.join(broken)}。**"
          f"那不是「沒有問題」，是這次沒撈到——請在報告裡標明。")
        a("")
    a("---")
    a("")
    a("## 給 GPT 的指示")
    a("")
    a(PROMPT)
    a("---")
    a("")
    a("## 原始資料")
    a("")
    for s in payload["sections"]:
        a(f"### {s['name']}（{s['construct']}）")
        a("")
        if s["error"]:
            a(f"⚠️ **這一段取得失敗：{s['error']}**　—— 不是沒問題，是沒撈到。")
            a("")
            continue
        if s.get("external"):
            a(f"🔌 **外部來源：{EXTERNAL_SOURCE}。本系統刻意不重複收集，欄位保留待介接。**")
        elif not s["has_data"]:
            a("❌ **本月無資料。**")
        a("")
        a("```json")
        a(json.dumps(s["data"], ensure_ascii=False, indent=2, default=str))
        a("```")
        a("")

    if previous:
        a("---")
        a("")
        a(f"## 上月（{previous['year_month']}）快照，供比較")
        a("")
        a(f"產生於 {previous['generated_at']}。")
        a("")
        a("```json")
        a(json.dumps(previous.get("summary") or {}, ensure_ascii=False,
                     indent=2, default=str))
        a("```")
        a("")
    else:
        a("---")
        a("")
        a("## 上月快照")
        a("")
        a("**沒有上月快照**（這是第一次產生，或上個月沒產）。"
          "所以「跟上個月比」那一題**這次答不出來**，請直接這樣寫，不要用其他方式估。")
        a("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 凍結快照（決策 A：同月覆蓋，但記下每次的產生時間與產生者）
# ---------------------------------------------------------------------------

def _summary_of(payload: dict) -> dict:
    """快照只存彙總，不存明細——明細是當下的事實，下個月再看已經不同了，
    而且會讓快照表無限長大。跨月要比的是數字。"""
    out = {}
    for s in payload["sections"]:
        d = {k: v for k, v in s["data"].items()
             if isinstance(v, (int, float, str)) and not k.startswith("⚠️")}
        out[s["name"]] = {"has_data": s["has_data"], **d}
    return out


def freeze(conn, year_month: str, generated_by: str) -> dict:
    """凍結這個月的快照。同月重跑覆蓋（使用者 2026-09-08 決策 A）。

    覆蓋是刻意的：月報是當月結算，中途重產是正常的（資料還在補）。
    凍結的意義是「跨月比較有一致基準」，不是「當月不能改」。
    但每次覆蓋都會更新 `generated_at`／`generated_by`，
    「這份數字是什麼時候、誰產的」查得到。
    """
    payload = collect(conn)
    summary = _summary_of(payload)
    conn.execute(
        "INSERT INTO monthly_report_snapshot (year_month, generated_at, generated_by, "
        "summary_json, regenerated_count) VALUES (?,?,?,?,1) "
        "ON CONFLICT(year_month) DO UPDATE SET "
        "  generated_at = excluded.generated_at, "
        "  generated_by = excluded.generated_by, "
        "  summary_json = excluded.summary_json, "
        "  regenerated_count = monthly_report_snapshot.regenerated_count + 1",
        (year_month, payload["generated_at"], generated_by,
         json.dumps(summary, ensure_ascii=False)),
    )
    conn.commit()
    return get_snapshot(conn, year_month)


def get_snapshot(conn, year_month: str) -> dict | None:
    row = conn.execute(
        "SELECT year_month, generated_at, generated_by, summary_json, regenerated_count "
        "FROM monthly_report_snapshot WHERE year_month = ?", (year_month,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["summary"] = json.loads(d.pop("summary_json") or "{}")
    return d


def previous_month(year_month: str) -> str:
    y, m = int(year_month[:4]), int(year_month[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def list_snapshots(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT year_month, generated_at, generated_by, regenerated_count "
        "FROM monthly_report_snapshot ORDER BY year_month DESC")]
