"""系統校正＋實測與 CIA 不符清單（v1.473.0，2026-10-20）。

使用者：「如果原來的表寫 Windows，但實際上是 Linux，已經納管的話，應該要把 Windows 的資料校正過來」
「能幫它自動更正嗎？這就是納管的好處。哪些是被自動更正過的？可以做符號嗎？」

**CIA 匯入值一律不改**。「校正」＝畫面主值、分類、EOS 改用實測值，並標「⟲ 系統校正」；
CIA 原值並列，整理成清單交給 CIA 窗口更正。

兩種列：
  - corrected（系統校正）：證據夠強、已經拿來當顯示值的——SSH／WinRM 登入實測（確定）、
    RVTools guest OS（高）。哪個欄位可以被哪種證據校正寫在 correction_fields.json。
  - suspect（疑似不符）：只有存活掃描（或設定不允許校正的證據）講不一樣——**不自動校正**，只提示。

歷程：correction_log 記每一筆校正出現／CIA 已更正（重匯後 CIA 值＝校正值）／不再適用的時間。
「待 CIA 更正」永遠是即時算的（清單、匯出同一支 corrections()）；歷程只多記「CIA 已更正」。

只列 CIA 正本（不是 DYN-/VC-/AUTO-/ADOPT-）、非退役。
資產明細頁、這份清單、verify_numbers 用同一支 os_mismatch——不符的定義只有一份。
"""
from __future__ import annotations

import json
import os

HEADERS = ["項次", "資產序號", "主機名稱", "欄位", "CIA 原值", "校正值／實測值", "依據", "信心",
           "類型", "最後確認", "證據取自序號", "需誰處理", "要修改什麼"]

SRC_COLLECT = "collect"
SRC_RVTOOLS = "rvtools"
SRC_SCAN = "scan"

_CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "correction_fields.json")


def config() -> dict:
    with open(_CFG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _can_correct(cfg: dict, field: str, source: str) -> bool:
    return any(f["key"] == field and source in f.get("correct_by", []) for f in cfg["fields"])


def _label(cfg: dict, field: str) -> str:
    return next((f["label"] for f in cfg["fields"] if f["key"] == field), field)


def os_mismatch(measured: str | None, cia: str | None) -> bool:
    """實測 OS vs CIA 登記 OS 算不算不符。

    平台不同（Windows vs Linux vs AIX）一定不符；平台相同再比產品＋主版本（dyn_shell._os_mismatch，
    兩邊都認得才下結論）。任一邊空白＝不知道，不算不符（那是「CIA 沒填」，不是「填錯」）。
    """
    import dyn_shell
    import manage_state

    m, c = str(measured or "").strip(), str(cia or "").strip()
    if not m or not c:
        return False
    pm, pc = manage_state.platform_from_os(m), manage_state.platform_from_os(c)
    if pm and pc and pm != pc:
        return True
    return dyn_shell._os_mismatch(m, c)


def os_view(hw: dict, measured: dict | None) -> dict:
    """資產明細頁「作業系統」欄：實測優先，CIA 原值並列。

    優先序：人工指定（分類層）＞收集實測＞RVTools guest OS＞CIA。
    basis：measured＝SSH／WinRM 登入實測；rvtools＝vCenter（RVTools）回報；cia＝照 CIA 登記。
    effective：畫面主值，也是 EOS／生命週期查表用的字樣。corrected＝跟 CIA 不符且已拿實測當主值。
    """
    cia = str(hw.get("os_cia") or hw.get("os") or "").strip()
    basis = None
    if measured and measured.get("source") == SRC_RVTOOLS:
        basis = "rvtools"
    elif measured and measured.get("strong"):
        basis = "measured"
    if basis:
        eff = measured.get("os") or ""
        mm = os_mismatch(eff, cia)
        src = SRC_RVTOOLS if basis == "rvtools" else SRC_COLLECT
        return {"basis": basis, "effective": eff, "cia": cia or None, "mismatch": mm,
                "corrected": mm and _can_correct(config(), "os", src),
                "collected_at": measured.get("collected_at"),
                "via_serial": measured.get("via_serial")}
    return {"basis": "cia", "effective": str(hw.get("os") or "").strip(), "cia": cia or None,
            "mismatch": False, "corrected": False, "collected_at": None, "via_serial": None}


def ip_view(hw: dict, measured: dict | None) -> dict | None:
    """實測（真的 SSH 進去的那個位址）跟 CIA 登記 IP 不同時回 {measured, cia, via_serial}；否則 None。"""
    if not (measured and measured.get("strong") and measured.get("source") == SRC_COLLECT):
        return None
    mip = str(measured.get("via_ip") or "").strip()
    cip = str(hw.get("ip") or "").strip()
    if not mip or mip == "0.0.0.0" or not cip or mip == cip:
        return None
    return {"measured": mip, "cia": cip, "via_serial": measured.get("via_serial"),
            "collected_at": measured.get("collected_at")}


def hostname_fix_state(conn) -> dict:
    """hostname_fix.compute（關掉時 fixes 空、ip_errors 照列——那是提示，不改任何判斷）。"""
    import hostname_fix

    try:
        st = hostname_fix.compute(conn)
    except Exception:  # noqa: BLE001 - 舊庫算不出來：沒有主機名校正，其餘照常
        return {"fixes": {}, "ip_errors": []}
    if not hostname_fix.enabled(conn):
        return {"fixes": {}, "ip_errors": st["ip_errors"]}
    return st


def hostname_view(hw: dict, state: dict) -> dict | None:
    """這筆 CIA 的主機名被判成打錯字（hostname_fix）→ {measured, cia, collected_at, tier, via}。"""
    f = (state.get("fixes") or {}).get(hw.get("asset_serial"))
    if not f:
        return None
    return {"measured": f["measured"], "cia": f["cia"], "collected_at": f["at"], "tier": f["tier"],
            "via": f["via"], "ip": f["ip"]}


def ip_error_view(hw: dict, state: dict) -> dict | None:
    """疑似 CIA IP 填錯（C 實際在 Y）或主機名疑似不符（證據層級不允許校正）。"""
    return next((e for e in state.get("ip_errors") or [] if e["asset_serial"] == hw.get("asset_serial")),
                None)


def _rec(hw, field, cia, value, source, at, via, kind, cfg) -> dict:
    src = cfg["sources"].get(source, {})
    return {"asset_serial": hw["asset_serial"], "hostname": hw.get("hostname"),
            "field_key": field, "field": _label(cfg, field),
            "cia": "" if cia is None else str(cia), "measured": "" if value is None else str(value),
            "source_key": source, "source": src.get("label", source),
            "confidence": "疑似不符" if kind == "suspect" else src.get("confidence", ""),
            "kind": kind, "at": at, "via_serial": via, "against": "CIA",
            "needs": _needs(field, kind)}


def _needs(field: str, kind: str) -> str:
    """需誰處理：系統校正過、對照 CIA 的＝CIA 要改（主機名也是——以真實主機名為主，使用者 2026-10-21）。
    疑似不符與 RVTools 舊值＝資訊，不用人改（呼叫端會覆寫 against=RVTools 的列）。"""
    if kind != "corrected":
        return ""
    return "CIA"


# 要修改什麼（3-15 一欄、匯出一欄）：用白話講這一列要動哪個欄位（使用者 2026-10-21「再加一欄，要修改什麼，
# 譬如這個就是修改主機名稱」）。每列＝一台的一個欄位；同一台好幾個欄位不符就是好幾列，各寫各的。
ACTION_WORD = {"hostname": "修改主機名稱", "ip": "修改 IP", "os": "修改作業系統", "mac": "修改 MAC",
               "cpu": "修改 CPU 數", "mem": "修改記憶體", "disk": "修改磁碟總量", "model": "修改設備機型"}


def action(r: dict) -> str:
    """一列的「要修改什麼」。需要人處理的＝修改＋欄位；對照 RVTools 的＝vCenter 舊值，CIA 不用改；
    疑似不符（沒人要改）＝待確認＋欄位；歷程已關閉的照結果講。認不得的欄位＝不知道（不留白）。"""
    fk = r.get("field_key") or ""
    word = ACTION_WORD.get(fk)
    if fk == "_error" or not fk:
        return "不知道"
    what = word[2:] if word else (r.get("field") or "")
    if not what:
        return "不知道"
    kind = r.get("kind")
    if kind == "cia_fixed":
        return "不用改（CIA 已改好）"
    if kind == "gone":
        return "不用改（不再適用）"
    if r.get("against") == "RVTools":
        return "不用改 CIA（vCenter 舊值）"
    if r.get("needs"):
        return word or f"修改{what}"
    return f"待確認{what}"


def rows(conn) -> list[dict]:
    """全站 系統校正（corrected）＋疑似不符（suspect）明細，每台每欄一列。

    排序固定（序號 → 欄位 → 類型）；項次照這個順序編、不隨畫面重排而變。"""
    import collected_os
    import dyn_shell
    import lifecycle
    import manage_state
    import system_stats

    cfg = config()
    hw_rows = [dict(r) for r in conn.execute("SELECT * FROM hardware")]
    measured = collected_os.measured_map(conn)
    rv_map = collected_os.rvtools_map(conn, hw_rows)
    hfs = hostname_fix_state(conn)
    groups: dict[str, list[str]] = {}
    for g in system_stats.collapse_machines(hw_rows).values():
        serials = [r["asset_serial"] for r in g]
        for s in serials:
            groups[s] = serials

    out: list[dict] = []
    for hw in hw_rows:
        s = hw["asset_serial"]
        if not s or str(s).startswith(lifecycle.NON_CIA_PREFIXES):
            continue
        if (hw.get("asset_status") or "").strip() in lifecycle.RETIRED_STATUS:
            continue
        done: set[str] = set()          # 已經由較強證人處理過的欄位，掃描不再重列
        m = measured.get(s)
        ov = os_view(hw, m)
        if ov["basis"] != "cia":
            src = SRC_COLLECT if ov["basis"] == "measured" else SRC_RVTOOLS
            done.add("os")
            if ov["mismatch"]:
                out.append(_rec(hw, "os", ov["cia"], ov["effective"], src, ov["collected_at"],
                                ov["via_serial"], "corrected" if ov["corrected"] else "suspect", cfg))
        iv = ip_view(hw, m)
        if iv:
            done.add("ip")
            kind = "corrected" if _can_correct(cfg, "ip", SRC_COLLECT) else "suspect"
            out.append(_rec(hw, "ip", iv["cia"], iv["measured"], SRC_COLLECT, iv["collected_at"],
                            iv["via_serial"], kind, cfg))
        hv = hostname_view(hw, hfs)
        if hv:
            done.add("hostname")
            out.append(_rec(hw, "hostname", hv["cia"], hv["measured"], hv["tier"], hv["collected_at"],
                            hv["via"], "corrected", cfg))
        ie = ip_error_view(hw, hfs)
        if ie and not (iv and ie.get("elsewhere_ip")):     # IP 已經由登入實測校正了，不再重提 IP 填錯
            done.add("hostname")
            if ie.get("elsewhere_ip"):
                rec = _rec(hw, "ip", ie["cia_ip"], f"{ie['cia_hostname']} 實際在 {ie['elsewhere_ip']}",
                           ie["tier"], ie["at"], ie["via"], "suspect", cfg)
                rec["needs"] = "CIA"
                rec["note"] = (f"疑似：CIA IP 填錯（{ie['cia_hostname']} 實際在 {ie['elsewhere_ip']}；"
                               f"{ie['cia_ip']} 上的機器講自己是 {ie['measured_hostname']}）")
            else:
                rec = _rec(hw, "hostname", ie["cia_hostname"], ie["measured_hostname"], ie["tier"],
                           ie["at"], ie["via"], "suspect", cfg)
            out.append(rec)
        rv = rv_map.get(s)
        if rv and "os" not in done:
            cia_os = str(hw.get("os_cia") or hw.get("os") or "").strip()
            pr, pc = (manage_state.platform_from_os(rv.get("guest_os")),
                      manage_state.platform_from_os(cia_os))
            if pr and pc and pr != pc:      # RVTools 只在平台不同時才列（版本差多半是設定檔類型，不是真的不符）
                done.add("os")
                out.append(_rec(hw, "os", cia_os, rv.get("guest_os"), SRC_RVTOOLS, rv.get("imported_at"),
                                rv.get("resolved_serial"),
                                "corrected" if _can_correct(cfg, "os", SRC_RVTOOLS) else "suspect", cfg))
        if rv:
            cip = str(hw.get("ip") or "").strip()
            if "ip" not in done and rv.get("ip") and cip and cip != "0.0.0.0" and rv["ip"] != cip:
                done.add("ip")
                out.append(_rec(hw, "ip", cip, rv["ip"], SRC_RVTOOLS, rv.get("imported_at"),
                                rv.get("resolved_serial"),
                                "corrected" if _can_correct(cfg, "ip", SRC_RVTOOLS) else "suspect", cfg))
        try:
            obs = dyn_shell.observed_facts(conn, groups.get(s, [s]), hw)
        except Exception:  # noqa: BLE001 - 掃描證據壞一台不擋整份清單；那台只少了掃描這一欄
            obs = None
        for f in (obs or {}).get("fields", []):
            if not f.get("mismatch") or f["key"] in done:
                continue
            out.append(_rec(hw, f["key"], f.get("registered"), f.get("observed"), SRC_SCAN,
                            obs.get("seen_at"), obs.get("via_serial"), "suspect", cfg))
    # 硬體欄位（CPU／記憶體／磁碟／型號）：實測 vs RVTools、實測 vs CIA（measured_hw）
    try:
        import measured_hw
        out.extend(measured_hw.rows(conn, hw_rows, cfg, _rec))
    except Exception as exc:  # noqa: BLE001 - 硬體比對壞了不擋 OS／IP／主機名清單，但要看得到
        out.append({"asset_serial": "", "hostname": None, "field_key": "_error", "field": "硬體比對失敗",
                    "cia": "", "measured": str(exc)[:200], "source_key": "", "source": "", "confidence": "",
                    "kind": "suspect", "at": None, "via_serial": None, "against": ""})
    order = {f["key"]: i for i, f in enumerate(cfg["fields"])}
    out.sort(key=lambda r: (r["asset_serial"], order.get(r["field_key"], 99), r["kind"] != "corrected"))
    for i, r in enumerate(out, 1):
        r["no"] = i
        r["action"] = action(r)
    return out


def corrections(conn) -> list[dict]:
    """目前「系統校正（CIA 待更正）」的列——清單、匯出、驗數同一支。"""
    return [r for r in rows(conn) if r["kind"] == "corrected"]


KIND_LABEL = {"corrected": "系統校正", "suspect": "疑似不符", "cia_fixed": "CIA 已更正", "gone": "不再適用"}


def as_table(items: list[dict]) -> list[list]:
    return [[r["no"], r["asset_serial"], r["hostname"] or "", r["field"], r["cia"], r["measured"],
             r["source"], r["confidence"], KIND_LABEL.get(r["kind"], r["kind"]), r.get("at") or "",
             r.get("via_serial") or "", r.get("needs") or "", r.get("action") or action(r)] for r in items]


# ---- 歷程：CIA 重匯後已更正的要留紀錄（移到「CIA 已更正」）----

LOG_SQL = """
CREATE TABLE IF NOT EXISTS correction_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_serial    TEXT NOT NULL,
    field_key       TEXT NOT NULL,
    cia_value       TEXT,
    corrected_value TEXT,
    source_key      TEXT,
    evidence_at     TEXT,
    first_seen      TEXT NOT NULL,
    last_confirmed  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open',
    closed_at       TEXT,
    closed_cia      TEXT
);
CREATE INDEX IF NOT EXISTS idx_correction_log_open ON correction_log(status, asset_serial, field_key);
"""


def _same(field: str, a, b) -> bool:
    import system_stats

    a, b = str(a or "").strip(), str(b or "").strip()
    if field == "os":
        return bool(a) and bool(b) and not os_mismatch(a, b)
    if field == "hostname":
        return system_stats.strip_domain(a).lower() == system_stats.strip_domain(b).lower()
    return a == b


def sync_log(conn, now: str | None = None, current: list[dict] | None = None) -> dict:
    """把目前的校正寫進歷程：新出現的開一筆、還在的更新確認時間、消失的關閉
    （CIA 現在的值＝校正值 → cia_fixed；否則 gone）。冪等。只動 correction_log，不碰 hardware。"""
    from db import _now_local

    now = now or _now_local()
    conn.executescript(LOG_SQL)
    cur_rows = corrections(conn) if current is None else current
    cur = {(r["asset_serial"], r["field_key"]): r for r in cur_rows}
    opened = closed = 0
    open_rows = {(r["asset_serial"], r["field_key"]): r for r in conn.execute(
        "SELECT * FROM correction_log WHERE status = 'open'")}
    for k, r in cur.items():
        if k in open_rows:
            conn.execute("UPDATE correction_log SET cia_value = ?, corrected_value = ?, source_key = ?, "
                         "evidence_at = ?, last_confirmed = ? WHERE id = ?",
                         (r["cia"], r["measured"], r["source_key"], r["at"], now, open_rows[k]["id"]))
        else:
            conn.execute("INSERT INTO correction_log (asset_serial, field_key, cia_value, corrected_value, "
                         "source_key, evidence_at, first_seen, last_confirmed) VALUES (?,?,?,?,?,?,?,?)",
                         (k[0], k[1], r["cia"], r["measured"], r["source_key"], r["at"], now, now))
            opened += 1
    for k, lr in open_rows.items():
        if k in cur:
            continue
        if not cur:
            break   # 一筆都沒有＝多半是證據暫時讀不到（不是 CIA 全改好了）：不關歷程，下次再判
        hw = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (k[0],)).fetchone()
        now_cia = None
        if hw is not None:
            d = dict(hw)
            now_cia = (d.get("os_cia") or d.get("os")) if k[1] == "os" else d.get(k[1])
        status = "cia_fixed" if hw is not None and _same(k[1], now_cia, lr["corrected_value"]) else "gone"
        conn.execute("UPDATE correction_log SET status = ?, closed_at = ?, closed_cia = ? WHERE id = ?",
                     (status, now, now_cia, lr["id"]))
        closed += 1
    conn.commit()
    return {"opened": opened, "closed": closed, "open": len(cur)}


def history(conn) -> list[dict]:
    """已關閉的校正（CIA 已更正／不再適用），新的在前。"""
    cfg = config()
    try:
        found = conn.execute(
            "SELECT l.*, h.hostname FROM correction_log l LEFT JOIN hardware h "
            "ON h.asset_serial = l.asset_serial WHERE l.status <> 'open' "
            "ORDER BY l.closed_at DESC, l.id DESC").fetchall()
    except Exception:  # noqa: BLE001 - 還沒有歷程表（從沒同步過）＝沒有歷程，不是錯誤
        return []
    out = []
    for i, r in enumerate(found, 1):
        src = cfg["sources"].get(r["source_key"] or "", {})
        out.append({"no": i, "asset_serial": r["asset_serial"], "hostname": r["hostname"],
                    "field_key": r["field_key"], "field": _label(cfg, r["field_key"]),
                    "cia": r["cia_value"] or "", "measured": r["corrected_value"] or "",
                    "source_key": r["source_key"], "source": src.get("label", r["source_key"] or ""),
                    "confidence": src.get("confidence", ""), "kind": r["status"],
                    "at": r["closed_at"], "first_seen": r["first_seen"], "closed_cia": r["closed_cia"],
                    "via_serial": None})
        out[-1]["action"] = action(out[-1])
    return out


# ---- CIA 回報表（交給 CIA 窗口更正）----

REPORT_HEADERS = ["項次", "CIA 資產編號", "主機名稱", "欄位", "CIA 現值", "應改為（實測值）", "依據與時間",
                  "建議動作", "其他 MAC"]
_ACTION = {"ip": "改 IP", "hostname": "改主機名", "os": "改作業系統", "model": "改設備機型", "mac": "改 MAC"}

REPORT_GUIDE = [
    ["WEBIT 實測與 CIA 不符回報表——使用說明"],
    [""],
    ["1", "「待 CIA 更正」：已納管（登入收集）或 RVTools 看到的值跟 CIA 登記不同，系統畫面已用實測值（⟲ 系統校正）。請 CIA 依「應改為」更正。"],
    ["2", "「新增登記」：網路上確實存在、我們已收到資料，但 CIA 沒有登記的機器。請 CIA 補登。"],
    ["3", "「依據與時間」：登入實測＝確定；RVTools（VMware Tools 回報）＝高。只有掃描推測的不列在這份表。"],
    ["4", "CIA 更正並重新匯入後，系統會自動把該筆移到「CIA 已更正」，不用回信通知。"],
    ["5", "有疑問的列（例如其實是 IP 回收、或 VIP）請在「備註」欄寫明，回傳給 WEBIT 窗口。"],
]


def cia_report(conn) -> dict:
    """{'fix': [...], 'add': [...]}：待 CIA 更正（against=CIA 的系統校正）＋新增登記（CIA 待補登、有實測證據）。"""
    fix = []
    for r in corrections(conn):
        if r.get("against", "CIA") != "CIA" or r["field_key"] not in _ACTION:
            continue
        fix.append([r["asset_serial"], r["hostname"] or "", r["field"], r["cia"], r["measured"],
                    f"{r['source']}（{r['confidence']}）@{r.get('at') or '—'}", _ACTION[r["field_key"]],
                    r.get("other_macs") or ""])
    add = []
    try:
        import existence
        for m in existence.machines_in(conn, "cia_pending")["items"]:
            ev = set(m.get("evidence") or [])
            if not ({"onboarded", "inv:vcenter"} & ev):
                continue        # 只有掃描看到的不叫 CIA 補登（證據不夠）
            what = "；".join(x for x in (f"IP {m.get('ip')}" if m.get("ip") else "",
                                        f"OS {m.get('os')}" if m.get("os") else "",
                                        f"MAC {m.get('mac')}" if m.get("mac") else "") if x)
            add.append(["（未登記）", m.get("hostname") or "", "整筆", "", what,
                        ("已納管" if "onboarded" in ev else "RVTools") + f"@{m.get('last_seen_any') or '—'}",
                        "新增登記", ""])
    except Exception as exc:  # noqa: BLE001 - 補登清單算不出來：照樣出更正表，在表裡講原因
        add.append(["", "", "（新增登記清單產生失敗）", "", str(exc)[:200], "", "", ""])
    return {"fix": fix, "add": add}
