"""機櫃「位置重疊」待處理清單（2026-10-24）：把重疊提醒變成可以一筆一筆處理掉的工作。

使用者：「我找到問題了，但系統不幫我解決」。所以每一組重疊（同櫃、U 位互相重疊、連在一起的設備）都附：
  · 每台的主機名、IP、機型、資產序號、系統、CIA 機櫃欄原值
  · 系統猜的原因與建議：
      同一台的管理卡 → 已在畫面上併掉（顯示成已處理）
      同一台實體機的分割區（同機型＋同 U＋Power／AIX 或共用機身序號）→ 建議「合併為同一台」
      重複登記（同主機名或同 IP 兩筆）→ 建議「留一筆」（另一筆標「這筆已不在櫃上」）
      現場圖說位置不同（4-5 最近一次匯入的現場圖把其中一台放在別處）→ 建議「位置更正」到現場圖的櫃／U
      其他 → 待現場確認
  · 動作（誰／何時／備註都留，可以從同一張清單復原）：
      合併為同一台：只影響畫面（重疊提醒、面板併成一塊），不碰去重、台數、CIA（危險區域 D3）
      這筆已不在櫃上：用既有的 rack_state_override（removed），會進 CIA 待更正
      位置更正到…：用既有的 rack_position_override，會進 CIA 待更正
      確認是兩台（半寬）：接受，不再提醒
      前後各一台（2026-11-01）：兩台背對背共用同一段 U（一台在正面、一台在背面）。指定哪一台在背面，其餘在正面；
        只影響畫面（正面／背面切換）與「不再報位置重疊」；每台照舊各算一台，共用的 U 只算佔用一次，用電重量各算各的
不直接改 CIA。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

ACTIONS = {"merge": "合併為同一台", "accept_half": "確認是兩台（半寬）", "removed": "這筆已不在櫃上", "relocate": "位置更正",
           "split": "拆開（其實是不同台）", "front_rear": "前後各一台"}
CAUSE_LABEL = {"mgmt": "同一台的管理卡", "partition": "同一台實體機的分割區", "duplicate": "重複登記",
               "diagram": "現場圖說位置不同", "other": "其他"}
SUGGEST = {"mgmt": "已在畫面上併成一台，不用處理", "partition": "建議「合併為同一台」", "duplicate": "建議留一筆：另一筆按「這筆已不在櫃上」",
           "diagram": "建議「位置更正」到現場圖的位置", "other": "待現場確認"}

SQL = """CREATE TABLE IF NOT EXISTS rack_overlap_decision (
    id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT NOT NULL, rack TEXT NOT NULL, keys_json TEXT NOT NULL,
    action TEXT NOT NULL, target_key TEXT, detail_json TEXT, note TEXT,
    decided_by TEXT NOT NULL, decided_at TEXT NOT NULL, undone_by TEXT, undone_at TEXT, undo_note TEXT)"""


RACK_STALE_TEXT = "這筆決定記的櫃（或其中的設備）現在對不上了（可能是櫃名對照改過、或設備已搬走），沒有套用；請到這一櫃重新確認"


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(SQL)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _keys_id(keys) -> str:
    return json.dumps(sorted(keys), ensure_ascii=False)


def active_decisions(conn: sqlite3.Connection) -> list[dict]:
    # 讀取不建表（Codex 71）：表不存在＝沒有任何人工決定；建表只在下面的寫入入口
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rack_overlap_decision'").fetchone() is None:
        return []
    import rack_layout
    names = rack_layout.rack_name_map(conn)
    out = []
    for r in conn.execute("SELECT * FROM rack_overlap_decision WHERE undone_at IS NULL ORDER BY id"):
        d = dict(r)
        # 同一實體櫃合一（2.043.14，GPT 審查 4）：決策存的櫃名（舊名 1-1 或當時的畫面名）一律換成現在的畫面櫃名再比，
        # 換名後舊決策照樣套得上；真的套不上（那幾台已不在這櫃）由 groups() 列進 rack_stale，不靜默失效
        d["rack_stored"] = d["rack"]
        d["rack"] = rack_layout.display_rack(names, d["room"], d["rack"])
        d["keys"] = json.loads(d["keys_json"])
        d["detail"] = json.loads(d["detail_json"] or "null")
        d["action_label"] = ACTIONS.get(d["action"], d["action"])
        out.append(d)
    return out


def resolved_by_rack(conn: sqlite3.Connection) -> dict[tuple, list[set]]:
    """「合併為同一台」「確認是兩台」的組，按（機房, 櫃）分：只在決策當下那一櫃有效（搬走後再重疊要重新判斷）。"""
    out: dict[tuple, list[set]] = {}
    for d in active_decisions(conn):
        if d["action"] in ("merge", "accept_half"):
            out.setdefault((d["room"], d["rack"]), []).append(set(d["keys"]))
    return out


def merged_sets(conn: sqlite3.Connection, room: str, rack: str) -> list[list[str]]:
    """這一櫃「合併為同一台」的組（畫面把面板併成一塊；台數照舊）。"""
    return [sorted(d["keys"]) for d in active_decisions(conn)
            if d["action"] == "merge" and d["room"] == room and d["rack"] == rack]


# ===================================================================== 系統判斷為同一台（2026-10-24）
# 使用者：「很明顯的不要叫我決定」。把握度高的自動併成同一台（只影響畫面與用電只算一次；台數、去重、CIA 不動）。
# 規則（兩台都要在同一櫃、U 位重疊；管理卡另外處理）：
#   b. 主機名一樣＋機型一樣          （例：3 筆「IBM Storage-FS5300」同 35U ＝ 一台 FS5300、3 個管理 IP）
#   c. Power／AIX：共用機身序號；或機型一樣＋U 完全一樣（例：fut01／sec2T 同一台 S922 的兩個 LPAR）
#   d. IP 一樣（堆疊交換器共用一個管理 IP 但在不同 U、是兩台實體機 → U 不重疊，不會被這條併掉）
# 使用者按過「拆開（其實是不同台）」的，以使用者為準；人工「合併為同一台」也以使用者為準。
AUTO_REASON = {"same_host": "主機名與機型都一樣", "power_frame": "Power 共用機身序號", "power_lpar": "Power／AIX 同機型同 U（分割區）",
               "same_ip": "同一個 IP、同一段 U"}


SUSPECT_MIN = 5          # 「同一段 1–2U 超過 4 筆」＝5 筆以上

_GENERIC_HOSTS = {"localhost", "unknown", "tbd", "server", "na", "none", "test", "host", "null"}


def _ipv4_set(*vals) -> set[str]:
    """登記的 IP（可能一欄好幾個）→ 合法、可當識別用的 IPv4；0.0.0.0、127.*、169.254.* 這類不算。"""
    import ipaddress
    import re as _re
    out = set()
    for v in vals:
        for t in _re.split(r"[\s,;，、/]+", str(v or "")):
            try:
                a = ipaddress.ip_address(t.strip())
            except ValueError:
                continue
            if a.version == 4 and not (a.is_unspecified or a.is_loopback or a.is_link_local):
                out.add(str(a))
    return out


def _norm_model(v) -> str:
    return " ".join(str(v or "").upper().split())


def _auto_reason(a: dict, b: dict) -> str | None:
    import blast_radius
    import rack_layout

    if not (a.get("u_start") and b.get("u_start")) or not rack_layout._overlap(a["u_start"], a["u_end"], b["u_start"], b["u_end"]):
        return None
    ma, mb = _norm_model(a.get("model")), _norm_model(b.get("model"))
    ha, hb = blast_radius.host_match_key(a.get("name")), blast_radius.host_match_key(b.get("name"))
    same_host = bool(ha and ha == hb and ha not in _GENERIC_HOSTS)
    same_model = bool(ma and ma == mb)
    if same_host and same_model:
        return "same_host"
    if a.get("machine_type") == "power" and b.get("machine_type") == "power":
        if set(a.get("hw_serials") or []) & set(b.get("hw_serials") or []):
            return "power_frame"
        if same_model and (a["u_start"], a["u_end"]) == (b["u_start"], b["u_end"]):
            return "power_lpar"
    # 同 IP：還要同機型，而且主機名一樣（或其中一筆沒有主機名）。
    # HA 防火牆一對（-01／-02）共用一個 VIP、同 U 同機型，是兩台：主機名不同就不併。
    if (_ipv4_set(a.get("ip")) & _ipv4_set(b.get("ip"))) and same_model and (same_host or not (ha and hb)):
        return "same_ip"
    return None


STALE_NOTE = "位置變動失效"
STALE_TEXT = "原決定因位置變動失效，請重新確認"


def _fr_valid(d: dict, by: dict) -> bool:
    """決定當時存了每台的 U 範圍（detail.spans）：只有「組員都還在這櫃、而且 U 範圍跟決定當時一模一樣」才算有效。
    任何一台搬走、改了 U（位置更正、CIA 重匯入改位置…）就失效；搬回原位也不會自動復活的部分，由寫入位置時
    標記失效（invalidate_for_keys）負責——這裡是讀取時的兜底，涵蓋所有改位置的路徑。沒存 spans 的舊決定不檢查。"""
    spans = (d.get("detail") or {}).get("spans")
    if not spans:
        return all(k in by for k in d["keys"])
    for k in d["keys"]:
        m = by.get(k)
        if m is None or [m.get("u_start"), m.get("u_end")] != list(spans.get(k) or []):
            return False
    return True


def front_rear_sets(mine: list[dict], by: dict) -> list[dict]:
    """這一櫃有效的「前後各一台」：[{keys, front, rear, decision_id}]。位置跟決定當時不同的決定視為失效（不收）。
    背面的 key 記在 detail.rear_keys（沒有就用 target_key）；其餘都是正面。"""
    out = []
    for d in mine:
        if d["action"] != "front_rear" or not _fr_valid(d, by):
            continue
        keys = [k for k in d["keys"] if k in by]
        rear_all = set((d.get("detail") or {}).get("rear_keys") or ([d["target_key"]] if d.get("target_key") else []))
        rear = sorted(k for k in keys if k in rear_all)
        front = sorted(k for k in keys if k not in rear_all)
        if rear and front:
            out.append({"keys": sorted(keys), "front": front, "rear": rear, "decision_id": d["id"]})
    return out


def stale_front_rear(mine: list[dict], by: dict) -> list[dict]:
    """還標著有效、但位置已經跟決定當時不同的前後各一台決定（讀取時判斷，不寫庫）。"""
    return [d for d in mine if d["action"] == "front_rear" and not _fr_valid(d, by)]


def invalidate_for_keys(conn: sqlite3.Connection, keys, by: str | None, new_pos: dict | None = None) -> int:
    """寫入某幾台的位置時呼叫（rack_layout.set_position／clear_position／undo_move；不 commit，跟呼叫端同一個交易）：
    把涉及這些設備的有效「前後各一台」決定標為失效（undone，undo_note 以「位置變動失效」開頭），之後搬回原位也不會自動復活，
    要重新確認。new_pos＝{key: (room, rack, u_start, u_end)}，給了而且位置其實沒變就不動。表還沒建＝沒有決定。"""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rack_overlap_decision'").fetchone() is None:
        return 0
    n = 0
    ks = set(keys)
    import rack_layout
    names = rack_layout.rack_name_map(conn)
    for r in conn.execute("SELECT * FROM rack_overlap_decision WHERE action = 'front_rear' AND undone_at IS NULL").fetchall():
        dk = set(json.loads(r["keys_json"]))
        hit = dk & ks
        if not hit:
            continue
        det = json.loads(r["detail_json"] or "null") or {}
        spans = det.get("spans") or {}
        if new_pos:
            same = all(k in new_pos and new_pos[k][0] == r["room"] and rack_layout.same_rack(names, r["room"], new_pos[k][1], r["rack"])
                       and list(new_pos[k][2:4]) == list(spans.get(k) or []) for k in hit)
            if same:
                continue
        conn.execute("UPDATE rack_overlap_decision SET undone_by = ?, undone_at = ?, undo_note = ? WHERE id = ? AND undone_at IS NULL",
                     (by or "系統", _now(), f"{STALE_NOTE}：{'、'.join(sorted(hit))} 的位置被更動", r["id"]))
        n += 1
    return n


def merge_plan(devs: list[dict], room: str, rack: str, acts: list[dict] | None = None, conn=None) -> dict[str, Any]:
    """這一櫃哪些設備畫成同一台：人工合併 ＋ 系統判斷（扣掉使用者拆開的）。管理卡（mgmt_pairs）不在這裡處理。
    回 merged：[{keys, head, reasons, auto, decision_id}]、ignore：不算重疊的組（合併的＋確認是兩台的）。"""
    import rack_layout

    if acts is None:
        acts = active_decisions(conn)
    mine = [d for d in acts if d["room"] == room and d["rack"] == rack]
    splits = [set(d["keys"]) for d in mine if d["action"] == "split"]
    manual = [(set(d["keys"]), d["id"]) for d in mine if d["action"] == "merge"]
    accept = [set(d["keys"]) for d in mine if d["action"] == "accept_half"]
    by_all = {d["key"]: d for d in devs}
    fr = front_rear_sets(mine, by_all)
    fr_all = [set(x["keys"]) for x in fr]
    mp = rack_layout.mgmt_pairs(devs)
    manual_keys = set().union(*[k for k, _ in manual]) if manual else set()
    # 疑似登記錯誤（2026-10-26）：同一段 1–2U 擠了 SUSPECT_MIN 筆以上（例：3-4U 登記了 10 台 DL380）
    # → 實體放不下，不可能是「同一台的好幾筆」也不可能是好幾台，一律不自動併、各算一台、列出來請人看。
    slots: dict[tuple, list] = {}
    for d in devs:
        if d["key"] in mp or d["key"] in manual_keys or not d.get("u_start"):
            continue
        if d["u_end"] - d["u_start"] + 1 <= 2:
            slots.setdefault((d["u_start"], d["u_end"]), []).append(d["key"])
    suspect = [{"keys": sorted(ks), "u": list(u), "reason": f"同一段 {rack_layout._u_txt(*u)} 登記了 {len(ks)} 筆"}
               for u, ks in sorted(slots.items()) if len(ks) >= SUSPECT_MIN]
    sus_keys = {k for sg in suspect for k in sg["keys"]}
    cand = [d for d in devs if d["key"] not in mp and d.get("u_start") and d["key"] not in sus_keys]
    parent = {d["key"]: d["key"] for d in cand}
    reasons: dict[str, set] = {}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    hit: set[frozenset] = set()
    for i, a in enumerate(cand):
        for b in cand[i + 1:]:
            if any(a["key"] in sp and b["key"] in sp for sp in splits + fr_all):
                continue                      # 使用者說是不同台／前後各一台（不自動併成同一台）
            why = _auto_reason(a, b)
            if why:
                ra, rb = find(a["key"]), find(b["key"])
                if ra != rb:
                    parent[rb] = ra
                hit.add(frozenset((a["key"], b["key"])))
                reasons.setdefault(a["key"], set()).add(why)
                reasons.setdefault(b["key"], set()).add(why)
    comps: dict[str, set] = {}
    for d in cand:
        comps.setdefault(find(d["key"]), set()).add(d["key"])
    by = {d["key"]: d for d in devs}

    def head_of(keys):
        # 代表＝在用且有瓦數的優先（用電只算代表那筆，不能挑到關機或沒規格的而少算），再來有硬體序號的
        # 最後用 asset_serial、key 定序（2026-10-26 審查 L1）：同名同條件時不能靠 set 的順序挑，
        # 否則同一份資料每次挑到不同的代表，對帳橋／4-5 的角色會跳
        ms_ = [by[k] for k in sorted(keys) if k in by]
        return sorted(ms_, key=lambda d: (d.get("state") != "active", d.get("w") is None, not d.get("hw_serials"),
                                          d["name"].lower(), d.get("asset_serial") or "", d["key"]))[0]["key"]
    merged = []
    for keys, did in manual:
        keys = {k for k in keys if k in by}
        if len(keys) >= 2:
            merged.append({"keys": sorted(keys), "head": head_of(keys), "reasons": ["人工合併"], "auto": False, "decision_id": did})
    taken = set().union(*[set(m["keys"]) for m in merged]) if merged else set()
    for keys in comps.values():
        keys = keys - taken
        # 只有「每一對都各自命中規則」的才自動併（A~B 靠 IP、B~C 靠主機名，不代表 A 和 C 是同一台 → 留給人判斷）
        ks = sorted(keys)
        if any(frozenset((x, y)) not in hit for i, x in enumerate(ks) for y in ks[i + 1:]):
            continue
        if len(keys) >= 2:
            rs = sorted({AUTO_REASON[r] for k in keys for r in reasons.get(k, ())})
            merged.append({"keys": sorted(keys), "head": head_of(keys), "reasons": rs, "auto": True, "decision_id": None})
    ignore = [set(m["keys"]) for m in merged] + accept
    # 前後各一台：只放過「正面 × 背面」的配對（同一面互相重疊仍照報）
    for x in fr:
        ignore += [{f, r} for f in x["front"] for r in x["rear"]]
    # 工程系統（ODA／Exadata，2026-10-28）：同一套的多個節點本來就登記在同一段 U（整套一個機箱或一組框），
    # 不是位置重疊、也不是同一台——分類成「同一套、不同節點」：不進位置重疊待處理、不合併（各節點照算）。
    eng = engineered_groups(devs, {k for m in merged for k in m["keys"]} | set(mp))
    ignore += [set(g["keys"]) for g in eng]
    return {"merged": merged, "ignore": ignore, "suspect": suspect, "engineered": eng, "front_rear": fr}


ENGINEERED_LABEL = "同一套、不同節點"


def _is_engineered(d: dict) -> bool:
    """工程系統節點：型號規格表標了 system_level，或型號是一體機（對帳橋同一條 appliance_model_regex）。"""
    import re as _re

    import count_bridge
    if d.get("system_level"):
        return True
    rx = count_bridge.load_rules().get("appliance_model_regex") or r"$^"
    return bool(_re.search(rx, d.get("model") or "", _re.I))


def engineered_groups(devs: list[dict], skip: set) -> list[dict]:
    """同櫃、U 重疊、同型號的工程系統節點連成一組（skip＝已合併的、管理口）。回 [{keys, model, u_lo, u_hi}]。"""
    import rack_layout

    cand = [d for d in devs if d["key"] not in skip and d.get("u_start") and _is_engineered(d)]
    parent = {d["key"]: d["key"] for d in cand}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    for i, a in enumerate(cand):
        for b in cand[i + 1:]:
            if _norm_model(a.get("spec_model") or a.get("model")) == _norm_model(b.get("spec_model") or b.get("model")) \
                    and rack_layout._overlap(a["u_start"], a["u_end"], b["u_start"], b["u_end"]):
                parent[find(b["key"])] = find(a["key"])
    comp: dict[str, list] = {}
    for d in cand:
        comp.setdefault(find(d["key"]), []).append(d)
    return [{"keys": sorted(x["key"] for x in g), "model": g[0].get("model"),
             "u_lo": min(x["u_start"] for x in g), "u_hi": max(x["u_end"] for x in g)}
            for g in comp.values() if len(g) >= 2]


def _components(devs: list[dict], ov: dict[str, list[str]]) -> list[list[dict]]:
    by = {d["key"]: d for d in devs}
    seen: set = set()
    out = []
    for d in devs:
        if d["key"] in seen or d["key"] not in ov:
            continue
        comp, stack = [], [d["key"]]
        while stack:
            k = stack.pop()
            if k in seen or k not in by:
                continue
            seen.add(k)
            comp.append(by[k])
            stack.extend(ov.get(k, []))
        out.append(sorted(comp, key=lambda x: (-(x.get("u_end") or 0), x["name"].lower())))
    return out


def _diagram_index(conn: sqlite3.Connection, ms: list[dict]) -> dict[str, dict]:
    """4-5 每個機房最近一次匯入：系統機器 key → 現場圖的位置（只收位置不同的）。沒匯入過就是空的。"""
    import rack_diagram
    import rack_layout

    out: dict[str, dict] = {}
    rules = rack_diagram.alias_rules(conn)
    sysd = None
    for site in rack_diagram.SITES:
        imp = rack_diagram.last_import(conn, site)
        if not imp:
            continue
        sysd = sysd or rack_diagram._Sys(conn, ms)
        rc = rack_diagram.reconcile(conn, imp["id"], sysd)
        for x in rc["rows"]:
            if x.get("sys_key") and x["result"] in ("機櫃不同", "同櫃但U位不同"):
                # 現場圖的櫃名換回 CIA 的寫法（內湖 B3 → 2-3），位置更正才會落在同一個櫃
                cia_rack = next((m["rack"] for m in ms if m.get("rack") and rack_diagram.site_of(m["room"]) == site
                                 and rack_diagram.alias(site, m["rack"], rules) == x["rack_norm"]), x["rack"])
                out[x["sys_key"]] = {"rack": cia_rack, "diagram_rack": x["rack"], "u_start": x["u_start"],
                                     "u_end": x["u_end"], "import_id": imp["id"], "source": x.get("source") or x["sheet"],
                                     "result": x["result"]}
    return out


def _classify(devs: list[dict], diag: dict[str, dict]) -> tuple[str, dict]:
    import blast_radius

    hosts = [blast_radius.host_match_key(d["name"]) for d in devs]
    ips = [d.get("ip") for d in devs if d.get("ip")]
    if len(set(h for h in hosts if h)) < len([h for h in hosts if h]) or len(set(ips)) < len(ips):
        return "duplicate", {}
    moved = {d["key"]: diag[d["key"]] for d in devs if d["key"] in diag}
    if moved:
        return "diagram", moved
    models = {(d.get("model") or "").strip().upper() for d in devs}
    spans = {(d.get("u_start"), d.get("u_end")) for d in devs}
    shared_serial = len(set().union(*[set(d.get("hw_serials") or []) for d in devs])) < sum(len(d.get("hw_serials") or []) for d in devs)
    if len(models) == 1 and "" not in models and len(spans) == 1 and (
            all(d.get("machine_type") == "power" for d in devs) or shared_serial):
        return "partition", {}
    return "other", {}


def groups(conn: sqlite3.Connection, room: str | None = None, rack: str | None = None,
           ms: list[dict] | None = None) -> dict[str, Any]:
    """待處理的重疊組＋已處理的紀錄。room／rack 給了就只看那裡。"""
    import rack_layout

    # 範圍說明：groups 自己新增的程式碼不建表（本函式不再 ensure_table；rack_overlap_decision 表不存在就當沒有人工決定，建表只在 decide／undo）。
    # 但整條呼叫鏈不保證唯讀：下面的 rack_layout.machines 與 _diagram_index→rack_diagram.last_import 仍會建表／寫庫（既有行為，未動）。
    ms = ms if ms is not None else rack_layout.machines(conn)
    acts = active_decisions(conn)
    stale_rows = _stale_rows(conn)
    diag = _diagram_index(conn, ms)
    racks: dict[tuple, list] = {}
    for m in ms:
        if m["kind"] == "rack" and m.get("rack") and (not room or m["room"] == room) and (not rack or m["rack"] == rack):
            racks.setdefault((m["room"], m["rack"]), []).append(m)
    out, mgmt, same_box, engineered, front_rear = [], [], [], [], []
    for (rm, rk), devs in sorted(racks.items(), key=lambda x: (x[0][0], rack_layout._natural(x[0][1]))):
        plan = merge_plan(devs, rm, rk, acts)
        byk = {d["key"]: d for d in devs}
        mine_acts = [d for d in acts if d["room"] == rm and d["rack"] == rk]
        stale_ids = {d["id"] for d in stale_front_rear(mine_acts, byk)}
        stale_keys = [set(d["keys"]) for d in stale_front_rear(mine_acts, byk)] + \
                     [set(r["keys"]) for r in stale_rows if r["room"] == rm and r["rack"] == rk]
        for mg in plan["merged"]:
            same_box.append({"room": rm, "rack": rk, "keys": mg["keys"], "head": mg["head"], "reasons": mg["reasons"],
                             "auto": mg["auto"], "decision_id": mg["decision_id"],
                             "devices": [{"key": byk[k]["key"], "name": byk[k]["name"], "ip": byk[k].get("ip"), "model": byk[k].get("model"), "serial": byk[k]["asset_serial"], "hw_serials": byk[k].get("hw_serials") or [], "systems": byk[k].get("api_ids") or [], "rack_raw": byk[k].get("rack_raw"), "u": rack_layout._u_text(byk[k]), "u_start": byk[k]["u_start"], "u_end": byk[k]["u_end"], "state": byk[k].get("state_label") or byk[k].get("asset_status")} for k in mg["keys"]]})
        for x in plan.get("front_rear", []):
            front_rear.append({"room": rm, "rack": rk, "keys": x["keys"], "front": x["front"], "rear": x["rear"],
                               "decision_id": x["decision_id"], "u": rack_layout._u_text(byk[x["keys"][0]]),
                               "names": {k: byk[k]["name"] for k in x["keys"]}})
        mp = rack_layout.mgmt_pairs(devs)
        for ok, v in mp.items():
            o = next(d for d in devs if d["key"] == ok)
            h = next(d for d in devs if d["key"] == v["host"])
            mgmt.append({"room": rm, "rack": rk, "u": rack_layout._u_text(h), "host": h["name"], "card": o["name"],
                         "card_ip": o.get("ip"), "confirmed": v["confirmed"]})
        ov = rack_layout.overlaps([d for d in devs if d["key"] not in mp])
        ov_left = rack_layout.overlaps([d for d in devs if d["key"] not in mp], plan["ignore"])
        eng_sets = [set(g["keys"]) for g in plan.get("engineered", [])]
        for g in plan.get("engineered", []):
            engineered.append({"room": rm, "rack": rk, "keys": g["keys"], "model": g["model"],
                               "u": f"{g['u_lo']}-{g['u_hi']}U", "label": ENGINEERED_LABEL,
                               "names": [byk[k]["name"] for k in g["keys"]]})
        for comp in _components(devs, ov):
            keys = {d["key"] for d in comp}
            if any(keys <= es for es in eng_sets):
                continue                      # 同一套、不同節點：不是位置重疊，不列待處理
            # 只認「同一櫃、組員完全一樣」的決策；組員變了（有台搬走／新進）就要重新判斷
            dec = next((d for d in acts if d["action"] in ("merge", "accept_half", "front_rear") and set(d["keys"]) == keys
                        and d["room"] == rm and d["rack"] == rk and d["id"] not in stale_ids), None)
            cause, extra = _classify(comp, diag)
            out.append({
                "id": f"{rm}|{rk}|{_keys_id(keys)}", "room": rm, "rack": rk,
                "u_lo": min(d["u_start"] for d in comp), "u_hi": max(d["u_end"] for d in comp),
                "keys": sorted(keys), "cause": cause, "cause_label": CAUSE_LABEL[cause], "suggest": SUGGEST[cause],
                "relocate": extra, "decision": dec,
                # 以前確認過「前後各一台」、但位置變動讓決定失效了：重疊重新出現時標出來請人重新確認
                "stale_front_rear": STALE_TEXT if (dec is None and any(keys & sk for sk in stale_keys)) else None,
                # 已處理＝組內每一對重疊都被「合併（人工或系統判斷）／確認是兩台」蓋掉了
                "resolved": not any(d["key"] in ov_left for d in comp),
                "auto": not dec and not any(d["key"] in ov_left for d in comp),
                "devices": [{"key": d["key"], "name": d["name"], "ip": d.get("ip"), "model": d.get("model"),
                             "serial": d["asset_serial"], "systems": d.get("api_ids") or [], "rack_raw": d.get("rack_raw"),
                             "u": rack_layout._u_text(d), "u_start": d["u_start"], "u_end": d["u_end"],
                             "state": d.get("state_label") or d.get("asset_status")} for d in comp]})
    done = [d for d in acts if d["action"] in ("removed", "relocate") and (not room or d["room"] == room)
            and (not rack or d["rack"] == rack)]
    # 換名後套不上的舊決策（GPT 審查 4）：決策的設備沒有一台在它記的那一櫃（換成畫面櫃名後）→ 列出來請人重新確認，不靜默失效
    where = {m["key"]: (m["room"], m.get("rack")) for m in ms if m["kind"] == "rack"}
    rack_stale = [{"id": d["id"], "room": d["room"], "rack": d["rack"], "rack_stored": d.get("rack_stored"), "keys": d["keys"],
                   "action": d["action"], "action_label": d["action_label"], "text": RACK_STALE_TEXT}
                  for d in acts if d["action"] in ("merge", "accept_half", "front_rear")
                  and (not room or d["room"] == room) and (not rack or d["rack"] == rack)
                  and not any(where.get(k) == (d["room"], d["rack"]) for k in d["keys"])]
    open_n = sum(1 for g in out if not g["resolved"])
    return {"groups": out, "open": open_n, "resolved": len(out) - open_n, "mgmt": mgmt, "done": done,
            "same_box": same_box, "auto_merged": sum(1 for x in same_box if x["auto"]), "engineered": engineered,
            "front_rear": front_rear, "rack_stale": rack_stale}


def _stale_rows(conn: sqlite3.Connection) -> list[dict]:
    """因位置變動被系統標為失效的前後各一台決定（讀取，表不存在＝沒有）。"""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rack_overlap_decision'").fetchone() is None:
        return []
    import rack_layout
    names = rack_layout.rack_name_map(conn)
    return [{**dict(r), "rack": rack_layout.display_rack(names, r["room"], r["rack"]), "keys": json.loads(r["keys_json"])}
            for r in conn.execute(
        "SELECT * FROM rack_overlap_decision WHERE action = 'front_rear' AND undone_at IS NOT NULL AND undo_note LIKE ?",
        (STALE_NOTE + "%",))]


def _state_row(conn, key):
    r = conn.execute("SELECT state, note, updated_by, updated_at FROM rack_state_override WHERE key = ?", (key,)).fetchone()
    return dict(r) if r else None


def _pos_row(conn, key):
    r = conn.execute("SELECT room, rack, u_start, u_end, note, updated_by, updated_at FROM rack_position_override "
                     "WHERE key = ? AND status = 'active'", (key,)).fetchone()
    return dict(r) if r else None


def decide(conn: sqlite3.Connection, room: str, rack: str, keys: list[str], action: str, by: str | None,
           note: str | None = None, target_key: str | None = None, relocate: dict | None = None) -> dict:
    """記一筆處理。removed／relocate 走既有的機櫃狀態／位置更正（各自也有稽核紀錄），不直接改 CIA。
    寫入前記下這台原本的標記／位置更正（復原時還原回去，不是清掉），寫入後記下設成什麼（復原前比對還是不是這筆設的）。
    全部在同一個交易裡：任何一步失敗就整筆退回。"""
    import rack_layout

    ensure_table(conn)
    if not by:
        raise PermissionError("要登入才能處理（處理人是稽核欄位）")
    if action not in ACTIONS:
        raise ValueError("動作要是：" + "／".join(ACTIONS.values()))
    cur = groups(conn, room, rack)
    if action == "split":
        sb = next((x for x in cur["same_box"] if x["auto"] and set(x["keys"]) == set(keys)), None)
        if not sb:
            raise LookupError("這一組現在不是「系統判斷為同一台」，請重新整理")
        g = {"keys": sb["keys"], "devices": sb["devices"], "decision": None}
    else:
        g = next((x for x in cur["groups"] if set(x["keys"]) == set(keys)), None)
        if not g and action == "merge":
            # 機櫃上 Ctrl 點選幾台直接合併：要都在這一櫃、至少兩台、還沒被併掉
            import rack_layout as _rl
            here = {m["key"]: m for m in _rl.machines(conn) if m["kind"] == "rack" and m["room"] == room and m.get("rack") == rack}
            taken = {k for b in cur["same_box"] for k in b["keys"]}
            if len(set(keys)) >= 2 and all(k in here for k in keys) and not (set(keys) & taken):
                g = {"keys": sorted(set(keys)), "decision": None, "resolved": False,
                     "devices": [{"key": k, "name": here[k]["name"], "serial": here[k]["asset_serial"], "u": "", "rack_raw": None}
                                 for k in sorted(set(keys))]}
        if not g:
            raise LookupError("這一組重疊已經不存在（可能已處理或資料變了），請重新整理")
        if action in ("merge", "accept_half", "front_rear") and (g["decision"] or g["resolved"]):
            raise ValueError("這一組已經處理過了（或系統已判斷為同一台）；要改請先復原或拆開")
    detail: dict[str, Any] = {}
    try:
        if action == "front_rear":
            if len(set(keys)) < 2 or target_key not in g["keys"]:
                raise ValueError("前後各一台：要指定組裡哪一台在背面")
            by_dev = {d["key"]: d for d in g["devices"]}
            front = sorted(k for k in g["keys"] if k != target_key)
            # 同一面不能互相重疊（兩台都在正面還重疊，那是真的位置衝突，不是前後各一台）
            for i, a in enumerate(front):
                for b in front[i + 1:]:
                    if rack_layout._overlap(by_dev[a]["u_start"], by_dev[a]["u_end"], by_dev[b]["u_start"], by_dev[b]["u_end"]):
                        raise ValueError(f"{by_dev[a]['name']} 和 {by_dev[b]['name']} 都在正面還互相重疊，請改指定背面的那一台，或先處理其中一台")
            detail = {"rear_keys": [target_key], "front_keys": front,
                      "names": {k: by_dev[k]["name"] for k in g["keys"]},
                      "spans": {k: [by_dev[k]["u_start"], by_dev[k]["u_end"]] for k in g["keys"]}}   # 決定當時的 U 範圍
            # 取代同一批設備上還掛著、但已失效的舊決定
            for old in [d for d in active_decisions(conn) if d["action"] == "front_rear" and d["room"] == room
                        and d["rack"] == rack and set(d["keys"]) & set(keys)]:
                conn.execute("UPDATE rack_overlap_decision SET undone_by = ?, undone_at = ?, undo_note = ? WHERE id = ? AND undone_at IS NULL",
                             (by, _now(), f"{STALE_NOTE}：被新的決定取代", old["id"]))
        elif action in ("removed", "relocate"):
            if target_key not in g["keys"]:
                raise ValueError("要指定是組裡的哪一台")
            pending = conn.execute("SELECT id FROM rack_overlap_decision WHERE target_key = ? AND undone_at IS NULL "
                                   "AND action IN ('removed','relocate')", (target_key,)).fetchone()
            if pending:
                raise ValueError(f"這台已經有一筆還沒復原的處理（#{pending['id']}），請先復原那筆")
            dev = next(d for d in g["devices"] if d["key"] == target_key)
            detail["device"] = {k: dev[k] for k in ("name", "serial", "u", "rack_raw")}
            if action == "removed":
                detail["prior_state"] = _state_row(conn, target_key)
                rack_layout.set_state_override(conn, target_key, "removed", f"位置重疊處理：{note or ''}".strip("："), by,
                                               commit=False)
                detail["set_state"] = _state_row(conn, target_key)
            else:
                rl = relocate or {}
                if not (note or "").strip():
                    raise ValueError("位置更正要寫備註（例：依 10-23 現場圖）")
                if not rl.get("rack") or not rl.get("u_start"):
                    raise ValueError("位置更正要填機櫃和起始 U")
                if rl.get("room") and rl["room"] != room:
                    raise ValueError("位置更正只能在同一個機房裡（跨機房請用 4-1 機櫃正面的「更正位置」）")
                new_rack = rack_layout._norm_rack(str(rl["rack"]).strip())
                racks_here = {m["rack"] for m in rack_layout.machines(conn) if m["room"] == room and m.get("rack")}
                if new_rack not in racks_here:
                    raise ValueError(f"{room} 沒有「{rl['rack']}」這一櫃（請用系統裡的櫃名，例：內湖用 2-3 不是 B3）")
                u_h = int(rl.get("u_height") or ((dev["u_end"] or 0) - (dev["u_start"] or 0) + 1) or 1)
                detail["prior_position"] = _pos_row(conn, target_key)
                rack_layout.set_position(conn, target_key, room, new_rack, int(rl["u_start"]), u_h, note.strip(), by, commit=False)
                detail["set_position"] = _pos_row(conn, target_key)
                detail["relocate"] = {"room": room, "rack": new_rack, "u_start": int(rl["u_start"]), "u_height": u_h}
        cur_id = conn.execute(
            "INSERT INTO rack_overlap_decision (room, rack, keys_json, action, target_key, detail_json, note, decided_by, decided_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)", (room, rack_layout.stored_rack(rack_layout.rack_name_map(conn), room, rack), _keys_id(keys), action, target_key,
                                           json.dumps(detail, ensure_ascii=False), (note or "").strip() or None, by, _now())).lastrowid
        conn.commit()
    except Exception:
        conn.rollback()      # 整筆退回，再把原本的錯誤往上丟（API 轉成 400／409）
        raise
    return {"id": cur_id, "action": action, "action_label": ACTIONS[action]}


def undo(conn: sqlite3.Connection, decision_id: int, by: str | None, note: str | None = None) -> dict:
    """復原一筆處理。removed／relocate：只有目前的標記／位置更正「還是這筆設的那一筆」（誰、何時都一樣）才動，
    而且是還原成處理前的樣子（原本有標記／更正就還原回去），不是清掉；別人後來改過就不動、回報。"""
    import rack_layout

    ensure_table(conn)
    if not by:
        raise PermissionError("要登入才能復原")
    d = conn.execute("SELECT * FROM rack_overlap_decision WHERE id = ?", (decision_id,)).fetchone()
    if not d:
        raise LookupError(f"找不到處理紀錄 {decision_id}")
    if d["undone_at"]:
        raise ValueError("這筆已經復原過了")
    det = json.loads(d["detail_json"] or "{}")
    warn = None
    key = d["target_key"]
    why = f"復原位置重疊處理 #{decision_id}"
    try:
        if d["action"] == "removed":
            now = _state_row(conn, key)
            if now and det.get("set_state") and now == det["set_state"]:
                prior = det.get("prior_state")
                rack_layout.set_state_override(conn, key, prior["state"] if prior else None,
                                               (prior or {}).get("note") or why, by, commit=False)
            else:
                warn = "這台的機櫃狀態後來被改過，沒有動它"
        elif d["action"] == "relocate":
            now = _pos_row(conn, key)
            if now and det.get("set_position") and now == det["set_position"]:
                prior = det.get("prior_position")
                if prior:
                    rack_layout.set_position(conn, key, prior["room"], prior["rack"], prior["u_start"],
                                             (prior["u_end"] - prior["u_start"] + 1) if prior["u_start"] else None,
                                             prior.get("note") or why, by, commit=False)
                else:
                    rack_layout.clear_position(conn, key, why, by, commit=False)
            else:
                warn = "這台的位置後來被改過，沒有動它"
        cur = conn.execute("UPDATE rack_overlap_decision SET undone_by = ?, undone_at = ?, undo_note = ? "
                           "WHERE id = ? AND undone_at IS NULL", (by, _now(), note or warn, decision_id))
        if cur.rowcount != 1:
            raise ValueError("這筆已經被復原了（可能有人同時按）")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"id": decision_id, "undone": True, "warning": warn}


def history(conn: sqlite3.Connection, room: str | None = None) -> list[dict]:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rack_overlap_decision'").fetchone() is None:
        return []                                  # 讀取不建表
    rows = conn.execute("SELECT * FROM rack_overlap_decision" + (" WHERE room = ?" if room else "") + " ORDER BY id DESC",
                        ((room,) if room else ())).fetchall()
    return [{**dict(r), "action_label": ACTIONS.get(r["action"], r["action"]), "keys": json.loads(r["keys_json"])} for r in rows]
