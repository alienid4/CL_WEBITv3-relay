"""事件應變 v1（2026-10-23 板橋 IDC UPS 電力切換異常之後做）。

那天的痛點：16:22 斷電 → 16:37 才有人手做一份 Excel 列出受影響主機（15 分鐘才知道是哪幾台）；
沒人知道還有哪台沒起來；每一組（網管／主機／DBA／儲存／AP）各自一份清單；每 30 分鐘的狀態回報手寫。
設計給「半夜一個值班的人拿手機」用：

1. 開事件（類型／標題／開始時間／備註）
2. 貼告警找關聯：貼告警文字／IP／主機名 → 對到機器 → 列出共同點（同機房／樓層／排／櫃／ESXi／叢集／網段），
   一鍵把共同點變成事件範圍
3. 範圍展開：實體設備＋這些 ESXi 上的 VM＋系統＋聯絡人（值班電話），單電源（型號規格 psu_count==1）先標出來
4. 一鍵測一輪（通／不通／無法測，留每一輪）——沿用 host_ping（只讀，不改任何資產資料）
5. 共用檢查清單（每個系統一列；沒有系統的機器一列），組別／狀態／備註／誰改的
6. 回報文字（手機聊天群組貼得下的短行）
7. 時間軸＋結案（結案後唯讀）＋匯出

不在 v1：機櫃→UPS／PDU 對應（沒有配電資料）、告警自動開事件＋通知值班（v2）。
只讀現有資料、只寫自己的 incident_* 表；台數／去重／identity 一律不動（沿用 rack_layout.machines）。
"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any

TYPES = ("電力", "網路", "主機", "其他")
#: 2.x 4.1 事件應變（2026-10-27）：新增事件時先選「計畫停機」或「異常事件」（舊事件都是異常事件）
CATEGORIES = {"planned": "計畫停機", "incident": "異常事件"}
#: Codex 21 號 #5：事件性質。新事件必須明選；歷史事件 mode 保持 NULL＝「未分類（歷史）」，不以標題猜
MODES = {"formal": "正式", "drill": "演練"}
UNCLASSIFIED = "未分類（歷史）"
TEAMS = ("網管", "主機", "DBA", "儲存", "AP")
TASK_STATUS = ("未聯絡", "檢查中", "OK", "異常")
RESULT_LABEL = {"alive": "通", "no_response": "不通", "unknown": "無法測"}
REPORT_EVERY_MIN = 30

SQL = [
    """CREATE TABLE IF NOT EXISTS incident_event (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL, title TEXT NOT NULL, started_at TEXT NOT NULL, note TEXT,
        status TEXT NOT NULL DEFAULT 'open',          -- open / closed
        scope_json TEXT, alerts_text TEXT,
        summary TEXT, created_by TEXT, created_at TEXT NOT NULL,
        closed_by TEXT, closed_at TEXT, next_report_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS incident_timeline (
        id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
        at TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, created_by TEXT)""",
    """CREATE TABLE IF NOT EXISTS incident_check_run (
        id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
        started_at TEXT NOT NULL, finished_at TEXT, total INTEGER, ok INTEGER, down INTEGER, unknown INTEGER,
        created_by TEXT)""",
    """CREATE TABLE IF NOT EXISTS incident_check_item (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL,
        key TEXT, name TEXT, ip TEXT, result TEXT NOT NULL, verdict TEXT, checked_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS incident_task (
        id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
        level TEXT NOT NULL, ref TEXT NOT NULL, name TEXT,
        team TEXT, status TEXT NOT NULL DEFAULT '未聯絡', note TEXT,
        updated_by TEXT, updated_at TEXT,
        UNIQUE(event_id, level, ref))""",
]


def ensure_tables(conn: sqlite3.Connection) -> None:
    for s in SQL:
        conn.execute(s)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(incident_event)")}
    if "category" not in cols:      # 2.x：計畫停機／異常事件（舊事件預設異常事件）
        conn.execute("ALTER TABLE incident_event ADD COLUMN category TEXT NOT NULL DEFAULT 'incident'")
    if "mode" not in cols:          # 2.032：正式／演練；可空，歷史事件不改寫
        conn.execute("ALTER TABLE incident_event ADD COLUMN mode TEXT")


def _has_mode(conn: sqlite3.Connection) -> bool:
    """舊庫（部署前的數字核對、服務尚未遷移）可能還沒有 mode 欄位：讀取要動態組 SELECT，不能 OperationalError。"""
    try:
        return "mode" in {r[1] for r in conn.execute("PRAGMA table_info(incident_event)")}
    except sqlite3.Error:
        return False


def mode_label(mode: str | None) -> str:
    return MODES.get(mode or "", UNCLASSIFIED)


def mode_counts(events: list[dict], open_only: bool = False) -> dict[str, int]:
    """正式／演練／未分類各自獨立計；演練不混進正式。"""
    c = {"formal": 0, "drill": 0, "unclassified": 0}
    for e in events:
        if open_only and e.get("status") != "open":
            continue
        c[e.get("mode") if e.get("mode") in MODES else "unclassified"] += 1
    return c


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ===================================================================== 1. 事件
def create_event(conn, type_: str, title: str, started_at: str | None, note: str | None, by: str | None,
                 category: str = "incident", mode: str | None = None) -> dict:
    ensure_tables(conn)
    if mode not in MODES:           # 必須明選：不給預設值，避免演練被當正式（或反之）
        raise ValueError("要選事件性質：正式或演練")
    if category not in CATEGORIES:
        raise ValueError("事件類別要是「計畫停機」或「異常事件」")
    if type_ not in TYPES:
        raise ValueError(f"類型要是 {'／'.join(TYPES)}")
    if not (title or "").strip():
        raise ValueError("要寫標題")
    now = _now()
    st = (started_at or "").strip().replace("T", " ") or now
    if len(st) == 16:
        st += ":00"
    cur = conn.execute("INSERT INTO incident_event (type,title,started_at,note,status,created_by,created_at,category,mode) "
                       "VALUES (?,?,?,?,'open',?,?,?,?)", (type_, title.strip(), st, note, by, now, category, mode))
    eid = cur.lastrowid
    _log(conn, eid, "open", f"開事件（{CATEGORIES[category]}／{MODES[mode]}）：{type_}｜{title.strip()}（開始 {st[:16]}）", by)
    conn.commit()
    return get_event(conn, eid)


def _log(conn, eid: int, kind: str, text: str, by: str | None) -> None:
    conn.execute("INSERT INTO incident_timeline (event_id,at,kind,text,created_by) VALUES (?,?,?,?,?)",
                 (eid, _now(), kind, text, by))


def list_events(conn) -> list[dict]:
    ensure_tables(conn)
    mcol = "mode" if _has_mode(conn) else "NULL AS mode"     # 舊庫還沒遷移：mode 一律 NULL＝未分類
    out = [dict(r) for r in conn.execute(
        f"SELECT id,type,title,started_at,status,created_by,created_at,closed_at,category,{mcol} FROM incident_event "
        "ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, started_at DESC")]
    for e in out:
        e["mode_label"] = mode_label(e["mode"])
    return out


def get_event(conn, eid: int) -> dict:
    ensure_tables(conn)
    r = conn.execute("SELECT * FROM incident_event WHERE id = ?", (eid,)).fetchone()
    if not r:
        raise LookupError(f"找不到事件 {eid}")
    d = dict(r)
    d.setdefault("mode", None)
    d["mode_label"] = mode_label(d["mode"])
    d["scope"] = json.loads(d.pop("scope_json") or "null") or empty_scope()
    return d


def _writable(conn, eid: int) -> dict:
    ev = get_event(conn, eid)
    if ev["status"] != "open":
        raise PermissionError("事件已結案，只能看不能改")
    return ev


def add_note(conn, eid: int, text: str, by: str | None) -> None:
    _writable(conn, eid)
    if not (text or "").strip():
        raise ValueError("要寫內容")
    _log(conn, eid, "note", text.strip(), by)
    conn.commit()


def close_event(conn, eid: int, summary: str, by: str | None) -> dict:
    _writable(conn, eid)
    if not (summary or "").strip():
        raise ValueError("結案要寫摘要")
    conn.execute("UPDATE incident_event SET status='closed', summary=?, closed_by=?, closed_at=? WHERE id=?",
                 (summary.strip(), by, _now(), eid))
    _log(conn, eid, "close", f"結案：{summary.strip()}", by)
    conn.commit()
    return get_event(conn, eid)


def timeline(conn, eid: int) -> list[dict]:
    ensure_tables(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM incident_timeline WHERE event_id = ? ORDER BY at, id", (eid,))]


# ===================================================================== 機器索引（共用）
class World:
    """一次讀進全站機器＋CI 圖的 VM→ESXi→叢集，給「貼告警」與「範圍展開」共用。"""

    def __init__(self, conn: sqlite3.Connection, ms: list[dict] | None = None):
        import blast_radius
        import model_specs
        import rack_layout as rl

        self.conn = conn
        self.ms = ms if ms is not None else rl.machines(conn)
        self.by_key = {m["key"]: m for m in self.ms}
        self.by_serial = {s: m for m in self.ms for s in m["serials"]}
        floor_map = rl._setting_json(conn, "rack_floor_map") or {}
        specs = model_specs.index(conn)
        self.by_ip: dict[str, list[dict]] = {}
        self.by_host: dict[str, list[dict]] = {}
        for m in self.ms:
            for ip in {m.get("ip"), *[x["ip"] for x in m.get("ips", [])]}:
                if ip:
                    self.by_ip.setdefault(ip.strip(), []).append(m)
            for h in {m["name"], *m.get("asset_names", [])}:
                k = blast_radius.host_match_key(h)
                if k:
                    self.by_host.setdefault(k, []).append(m)
            spec = model_specs.lookup(specs, m.get("model"))
            pc = spec.get("psu_count") if spec and not spec.get("is_generic") else None
            try:
                m["_psu"] = int(pc) if pc not in (None, "") else None
            except (TypeError, ValueError):
                m["_psu"] = None          # 規格表寫了看不懂的值＝不明，不猜
            m["_floor"] = (rl.floor_of(floor_map, m["rack"]) or m.get("floor_hint")) if m.get("rack") else None
            m["_row"] = rl.rack_prefix(m["rack"]) if m.get("rack") else None
        node = {r["node_id"]: dict(r) for r in conn.execute(
            "SELECT node_id, node_type, label, asset_serial FROM ci_node WHERE gone_at IS NULL "
            "AND node_type IN ('esxi','cluster')")}
        self.node = node
        self.vm_esxi: dict[str, str] = {}          # machine key → esxi node_id
        self.esxi_cluster: dict[str, str] = {}
        for r in conn.execute("SELECT src_node_id, dst_node_id, edge_type FROM ci_edge WHERE gone_at IS NULL "
                              "AND edge_type IN ('runs_on','member_of')"):
            if r["edge_type"] == "runs_on" and r["src_node_id"].startswith("hw:"):
                m = self.by_serial.get(r["src_node_id"][3:])
                if m and r["dst_node_id"] in node:
                    self.vm_esxi[m["key"]] = r["dst_node_id"]
            elif r["edge_type"] == "member_of" and r["src_node_id"] in node:
                self.esxi_cluster[r["src_node_id"]] = r["dst_node_id"]
        # ESXi 節點 → 它那台實體機
        self.esxi_host: dict[str, dict] = {}
        for nid, n in node.items():
            if n["node_type"] == "esxi" and n["asset_serial"] in self.by_serial:
                self.esxi_host[nid] = self.by_serial[n["asset_serial"]]
        self.host_esxi = {m["key"]: nid for nid, m in self.esxi_host.items()}
        # 每筆登記的 AP（user_name）與 SP（custodian）：聯絡頁只列受影響那幾台自己的人，不是整個系統的
        self.people: dict[str, dict[str, list[str]]] = {}
        for r in conn.execute("SELECT asset_serial, user_name, custodian FROM hardware"):
            d = self.people.setdefault(r["asset_serial"], {"ap": [], "sp": []})
            for k, col in (("ap", "user_name"), ("sp", "custodian")):
                v = (r[col] or "").strip()
                if v and v not in d[k]:
                    d[k].append(v)

    def people_of(self, m: dict) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {"ap": [], "sp": []}
        for s in m["serials"]:
            for k in ("ap", "sp"):
                for v in self.people.get(s, {}).get(k, []):
                    if v not in out[k]:
                        out[k].append(v)
        return out

    def host_of(self, m: dict) -> dict | None:
        """這台實際所在的實體機：VM＝它跑的 ESXi 那台；實體機＝自己；CIA 寫 VM 又對不到 ESXi＝None（位置不明）。"""
        esxi = self.vm_esxi.get(m["key"])
        if esxi:
            # ESXi 在 vCenter 有、CIA 沒登記實體機：用 ESXi 節點本身當主機（位置不明），不要說成「對不到 ESXi」
            return self.esxi_host.get(esxi) or {
                "key": esxi, "name": self.label(esxi), "asset_serial": None, "room": None, "rack": None,
                "_row": None, "u_start": None, "u_end": None, "_psu": None, "_no_cia": True}
        return None if self.is_vm(m) else m

    def is_vm(self, m: dict) -> bool:
        """VM：vCenter 看得到它跑在哪台 ESXi，或 CIA 機櫃欄／型號寫「(VM)」。"""
        return m["key"] in self.vm_esxi or m.get("kind") in ("vm", "vm_model")

    def label(self, nid: str | None) -> str | None:
        return (self.node.get(nid) or {}).get("label") or nid

    def place(self, m: dict) -> dict:
        """實際位置：VM 用它所在 ESXi 實體機的機房／排／櫃。"""
        esxi = self.vm_esxi.get(m["key"])
        phys = self.esxi_host.get(esxi) if esxi else m
        phys = phys or m
        esxi = esxi or self.host_esxi.get(m["key"])
        return {"room": phys.get("room"),
                "floor": phys.get("_floor"), "row": phys.get("_row"), "rack": phys.get("rack"),
                "esxi": esxi, "cluster": self.esxi_cluster.get(esxi) if esxi else None,
                "is_vm": self.is_vm(m),
                "segments": sorted({x["cidr"] for x in m.get("ips", []) if x.get("cidr")})}


# ===================================================================== 2. 貼告警找關聯
_RE_IP = re.compile(r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])")
# 主機名：英文字開頭、含數字（APP163-097、esxi12-34、APPDB01）；純英文字（UPS、ping）不算
_RE_HOST = re.compile(r"(?<![\w.-])[A-Za-z][A-Za-z0-9_]*\d[A-Za-z0-9_-]*(?![\w-])")


def extract(text: str) -> dict[str, list[str]]:
    """從貼上的告警文字抓 IPv4 與像主機名的字。保持出現順序、去重。"""
    t = text or ""
    ips = list(dict.fromkeys(_RE_IP.findall(t)))
    no_ip = _RE_IP.sub(" ", t)
    hosts = [h for h in dict.fromkeys(_RE_HOST.findall(no_ip)) if len(h) >= 4]
    return {"ips": ips, "hosts": hosts}


def resolve(world: World, text: str) -> dict[str, Any]:
    """每個 IP／主機名對到哪一台。對不到、對到多台都照實列出。"""
    import blast_radius

    ex = extract(text)
    found: dict[str, dict] = {}
    rows = []
    for kind, toks in (("ip", ex["ips"]), ("host", ex["hosts"])):
        for t in toks:
            cands = world.by_ip.get(t, []) if kind == "ip" else world.by_host.get(blast_radius.host_match_key(t) or "", [])
            uniq = list({m["key"]: m for m in cands}.values())
            if len(uniq) == 1:
                m = uniq[0]
                rows.append({"token": t, "kind": kind, "status": "ok", "key": m["key"], "name": m["name"]})
                found.setdefault(m["key"], m)
            elif uniq:
                rows.append({"token": t, "kind": kind, "status": "ambiguous",
                             "candidates": [{"key": m["key"], "name": m["name"]} for m in uniq]})
            elif kind == "ip":
                rows.append({"token": t, "kind": kind, "status": "none"})
            # 主機名對不到就不列（告警文字裡很多英數混合的字不是主機名，例如 3FM4、UPS 型號）
    return {"tokens": rows, "machines": list(found.values()), "extracted": ex}


DIMS = [  # (欄位, 中文, 越前面越具體)
    ("rack", "櫃"), ("esxi", "ESXi"), ("row", "排"), ("cluster", "叢集"),
    ("floor", "樓層"), ("segment", "網段"), ("room", "機房"),
]


def correlate(world: World, machines: list[dict]) -> list[dict]:
    """共同點排名：同一個值涵蓋越多台越前面；同樣多台時越具體越前面（櫃 > ESXi > 排 > …）。"""
    total = len(machines)
    groups: dict[tuple, set] = {}
    for m in machines:
        p = world.place(m)
        vals = {
            "room": [p["room"]] if p["room"] else [],
            "floor": [(p["room"], p["floor"])] if p["floor"] else [],
            "row": [(p["room"], p["row"])] if p["row"] else [],
            "rack": [(p["room"], p["rack"])] if p["rack"] else [],
            "esxi": [p["esxi"]] if p["esxi"] else [],
            "cluster": [p["cluster"]] if p["cluster"] else [],
            "segment": p["segments"],
        }
        for dim, vs in vals.items():
            for v in vs:
                groups.setdefault((dim, v if not isinstance(v, list) else tuple(v)), set()).add(m["key"])
    order = {d: i for i, (d, _) in enumerate(DIMS)}
    dim_label = dict(DIMS)
    out = []
    for (dim, v), keys in groups.items():
        if total > 1 and len(keys) < 2:
            continue
        if dim == "room":
            where, scope = f"{v}", {"rooms": [v]}
        elif dim in ("floor", "row", "rack"):
            room, x = v
            where = f"{room} {x}" + (" 排" if dim == "row" else "")
            scope = {{"floor": "floors", "row": "rows", "rack": "racks"}[dim]: [{"room": room, dim: x}]}
        elif dim in ("esxi", "cluster"):
            where = f"{dim_label[dim]} {world.label(v)}"
            scope = {{"esxi": "esxi", "cluster": "clusters"}[dim]: [v]}
        else:
            where, scope = f"網段 {v}", {"segments": [v]}
        out.append({"dim": dim, "dim_label": dim_label[dim], "value": v if not isinstance(v, tuple) else list(v),
                    "count": len(keys), "total": total, "keys": sorted(keys),
                    "text": f"{total} 台中 {len(keys)} 台在 {where}" if dim not in ("esxi", "cluster")
                    else f"{total} 台中 {len(keys)} 台跑在 {where}",
                    "scope": scope})
    out.sort(key=lambda d: (-d["count"], order[d["dim"]], str(d["value"])))
    return out


# ===================================================================== 2b. 由下往上的推論樹
def reason_tree(world: World, machines: list[dict]) -> dict[str, Any]:
    """告警機器 → 所在實體機（VM 用它的 ESXi）→ 櫃 → 排 → 機房，每層都有台數、可下鑽。
    推論（只講資料撐得住的）：
      · 同一櫃有 ≥2 台實體機受影響 →「疑似同櫃電源」；否則同一排 ≥2 台 →「疑似同排電源」
      · 只有 1 台實體機 →「疑似單台主機問題」
      · 在那一櫃／排，受影響的實體機多半是單電源，而同櫃／排的雙電源機器沒出現在告警裡 →「疑似單一迴路跳電」，
        那些雙電源機器列成「可能已少一路電，請現場看」
    """
    hosts: dict[str, dict] = {}
    unplaced: list[str] = []
    for m in machines:
        h = world.host_of(m)
        if not h:
            unplaced.append(m["key"])
            continue
        d = hosts.setdefault(h["key"], {"key": h["key"], "name": h["name"], "serial": h["asset_serial"],
                                        "room": h.get("room"), "rack": h.get("rack"), "row": h.get("_row"),
                                        "u": h.get("u_start"), "psu": h.get("_psu"),
                                        "is_esxi": h["key"] in world.host_esxi or bool(h.get("_no_cia")),
                                        "no_cia": bool(h.get("_no_cia")), "alert_keys": []})
        d["alert_keys"].append(m["key"])
    for d in hosts.values():
        d["count"] = len(d["alert_keys"])

    def group(field: str) -> list[dict]:
        g: dict[tuple, dict] = {}
        for d in hosts.values():
            if not d.get(field):
                continue
            k = (d["room"], d[field])
            x = g.setdefault(k, {"room": d["room"], field: d[field], "hosts": [], "alert_keys": []})
            x["hosts"].append(d["key"])
            x["alert_keys"] += d["alert_keys"]
        out = [{**x, "host_count": len(x["hosts"]), "count": len(x["alert_keys"])} for x in g.values()]
        return sorted(out, key=lambda x: (-x["host_count"], -x["count"], str(x[field])))

    racks, rows = group("rack"), group("row")
    rooms: dict[str, dict] = {}
    for d in hosts.values():
        x = rooms.setdefault(d["room"] or "未填", {"room": d["room"] or "未填", "hosts": [], "alert_keys": []})
        x["hosts"].append(d["key"])
        x["alert_keys"] += d["alert_keys"]
    rooms_l = sorted(({**x, "host_count": len(x["hosts"]), "count": len(x["alert_keys"])} for x in rooms.values()),
                     key=lambda x: -x["count"])

    verdicts: list[dict] = []
    focus = None
    if racks and racks[0]["host_count"] >= 2:
        r = racks[0]
        focus = ("rack", r)
        verdicts.append({"kind": "same_rack", "text": f"疑似同櫃電源：{r['room']} {r['rack']} 有 {r['host_count']} 台實體機受影響（{r['count']} 筆告警）",
                         "keys": r["alert_keys"]})
    elif rows and rows[0]["host_count"] >= 2:
        r = rows[0]
        focus = ("row", r)
        verdicts.append({"kind": "same_row", "text": f"疑似同排電源：{r['room']} {r['row']} 排有 {r['host_count']} 台實體機受影響（{r['count']} 筆告警）",
                         "keys": r["alert_keys"]})
    elif hosts and (len(hosts) == 1 or (lambda top, tot: top["count"] >= 2 and top["count"] * 2 >= tot)(
            max(hosts.values(), key=lambda d: d["count"]), sum(d["count"] for d in hosts.values()))):
        d = max(hosts.values(), key=lambda d: d["count"])
        where = "這台 ESXi 沒有 CIA 登記，不知道在哪一櫃" if d["no_cia"] else f"{d['room'] or '機房不明'} {d['rack'] or '櫃不明'}"
        others = len(hosts) - 1
        verdicts.append({"kind": "single_host", "text": f"疑似單台主機問題：{d['name']}（{where}）掉了 {d['count']} 台"
                                                        + (f"；另有 {others} 台主機各掉少數" if others else ""),
                         "keys": d["alert_keys"]})
    elif hosts:
        verdicts.append({"kind": "spread", "text": f"分散在 {len(hosts)} 台實體機、看不出同櫃或同排，先逐台確認", "keys": [k for d in hosts.values() for k in d["alert_keys"]]})
    if focus:
        field, grp = focus
        down = [hosts[k] for k in grp["hosts"]]
        single_down = [d for d in down if d["psu"] == 1]
        peers = [m for m in world.ms if not world.is_vm(m) and m.get("room") == grp["room"]
                 and (m.get("rack") if field == "rack" else m.get("_row")) == grp[field]
                 and m["key"] not in hosts and not m.get("retired") and m.get("kind") == "rack"]
        dual_up = [m for m in peers if (m.get("_psu") or 0) >= 2]
        if down and len(single_down) * 2 > len(down) and dual_up:
            verdicts.append({"kind": "one_feed", "text": f"疑似單一迴路跳電：受影響的 {len(down)} 台裡 {len(single_down)} 台是單電源，"
                                                         f"同{'櫃' if field == 'rack' else '排'}雙電源的 {len(dual_up)} 台沒出現在告警裡",
                             "keys": [d["key"] for d in single_down]})
            verdicts.append({"kind": "check_feed", "text": f"可能已少一路電，請現場看：{'、'.join(m['name'] for m in dual_up[:20])}",
                             "keys": [m["key"] for m in dual_up]})
    if unplaced:
        verdicts.append({"kind": "unplaced", "text": f"{len(unplaced)} 台是 VM 但 vCenter 資料裡找不到它跑在哪台 ESXi（CIA 寫 VM、跟 vCenter 那筆沒連上），位置不明",
                         "keys": unplaced})
    return {"verdicts": verdicts, "hosts": sorted(hosts.values(), key=lambda d: (-d["count"], d["name"])),
            "racks": racks, "rows": rows, "rooms": rooms_l, "unplaced": unplaced, "alerts": len(machines)}


def analyze_alerts(conn, eid: int, text: str, by: str | None, world: World | None = None) -> dict:
    ev = _writable(conn, eid)
    world = world or World(conn)
    r = resolve(world, text)
    conn.execute("UPDATE incident_event SET alerts_text = ? WHERE id = ?",
                 (((ev.get("alerts_text") or "") + "\n" + text).strip(), eid))
    ok = sum(1 for t in r["tokens"] if t["status"] == "ok")
    _log(conn, eid, "alerts", f"貼告警：抓到 {len(r['tokens'])} 個 IP／主機名，對到 {len(r['machines'])} 台（{ok} 個對得上）", by)
    conn.commit()
    return {"tokens": r["tokens"], "machines": [_brief(world, m) for m in r["machines"]],
            "common": correlate(world, r["machines"]), "tree": reason_tree(world, r["machines"])}


# ===================================================================== 3. 範圍
SCOPE_KEYS = ("rooms", "floors", "rows", "racks", "esxi", "clusters", "segments", "machines")


def empty_scope() -> dict:
    return {k: [] for k in SCOPE_KEYS}


def merge_scope(a: dict, b: dict) -> dict:
    out = {k: list(a.get(k) or []) for k in SCOPE_KEYS}
    for k in SCOPE_KEYS:
        for v in b.get(k) or []:
            if v not in out[k]:
                out[k].append(v)
    return out


def scope_text(world: World | None, s: dict) -> str:
    parts = []
    parts += [f"{r}" for r in s.get("rooms", [])]
    parts += [f"{x['room']} {x['floor']}" for x in s.get("floors", [])]
    parts += [f"{x['room']} {x['row']} 排" for x in s.get("rows", [])]
    parts += [f"{x['room']} {x['rack']}" for x in s.get("racks", [])]
    parts += [f"ESXi {world.label(x) if world else x}" for x in s.get("esxi", [])]
    parts += [f"叢集 {world.label(x) if world else x}" for x in s.get("clusters", [])]
    parts += [f"網段 {x}" for x in s.get("segments", [])]
    if s.get("machines"):
        parts.append(f"指定 {len(s['machines'])} 台")
    return "、".join(parts) or "（未設定）"


def set_scope(conn, eid: int, scope: dict, by: str | None, replace: bool = False) -> dict:
    ev = _writable(conn, eid)
    world = World(conn)
    clean = {k: list(scope.get(k) or []) for k in SCOPE_KEYS}
    # ESXi／叢集可以打名稱（esxi12-34）或節點代碼（esxi:ESXI12-34）；對不到就照原值留著（不會命中任何機器）
    for k, typ in (("esxi", "esxi"), ("clusters", "cluster")):
        lab = {(n["label"] or "").lower(): nid for nid, n in world.node.items() if n["node_type"] == typ}
        clean[k] = [x if x in world.node else lab.get(str(x).lower(), x) for x in clean[k]]
    new = clean if replace else merge_scope(ev["scope"], clean)
    conn.execute("UPDATE incident_event SET scope_json = ? WHERE id = ?", (json.dumps(new, ensure_ascii=False), eid))
    exp = expand(world, new)
    _sync_tasks(conn, eid, exp)
    _log(conn, eid, "scope", f"範圍{'改成' if replace else '加入'}：{scope_text(world, clean)} → 共 {exp['counts']['machines']} 台／"
                             f"{exp['counts']['systems']} 個系統", by)
    conn.commit()
    return exp


def _room_eq(placed: str | None, scoped: str | None) -> bool:
    """機房比對。舊事件範圍的「分公司」（W221-016 之前所有分點共用的鍵）涵蓋所有「分公司-XX」分點。"""
    if placed == scoped:
        return True
    import rack_layout
    return scoped == rack_layout.LEGACY_BRANCH_ROOM and rack_layout.is_branch_site_room(placed)


def _in_scope(world: World, m: dict, s: dict) -> bool:
    if m["key"] in s.get("machines", []):
        return True
    if world.is_vm(m):          # VM 只跟著 ESXi／叢集／網段／指定進來（它的機房欄是登記值，不是實體位置）
        return False
    pl = world.place(m)
    if any(_room_eq(pl["room"], r) for r in s.get("rooms", [])):
        return True
    if any(_room_eq(pl["room"], x["room"]) and pl["floor"] == x["floor"] for x in s.get("floors", [])):
        return True
    if any(_room_eq(pl["room"], x["room"]) and pl["row"] == x["row"] for x in s.get("rows", [])):
        return True
    if any(_room_eq(pl["room"], x["room"]) and pl["rack"] == x["rack"] for x in s.get("racks", [])):
        return True
    esxi = world.host_esxi.get(m["key"])
    if esxi and (esxi in s.get("esxi", []) or world.esxi_cluster.get(esxi) in s.get("clusters", [])):
        return True
    return False


def expand(world: World, s: dict) -> dict[str, Any]:
    """範圍 → 實體設備＋這些 ESXi 上的 VM＋網段內的機器＋系統＋聯絡人。"""
    seg = set(s.get("segments") or [])
    phys = [m for m in world.ms if m["kind"] not in ("removed",) and not m.get("retired") and _in_scope(world, m, s)]
    keys = {m["key"] for m in phys}
    esxi_in = {world.host_esxi[k] for k in keys if k in world.host_esxi}
    esxi_in |= set(s.get("esxi") or [])
    esxi_in |= {e for e, c in world.esxi_cluster.items() if c in (s.get("clusters") or [])}
    vms = [world.by_key[k] for k, e in world.vm_esxi.items() if e in esxi_in and k not in keys
           and not world.by_key[k].get("retired")]
    keys |= {m["key"] for m in vms}
    by_seg = [m for m in world.ms if seg and m["key"] not in keys and not m.get("retired")
              and seg & {x["cidr"] for x in m.get("ips", []) if x.get("cidr")}]
    allm = phys + vms + by_seg
    items = [_brief(world, m) for m in allm]
    systems = _systems(world, items)
    return {"machines": sorted(items, key=_prio_key), "systems": systems,
            "counts": {"machines": len(items), "physical": sum(1 for i in items if not i["is_vm"]),
                       "vms": sum(1 for i in items if i["is_vm"]), "systems": len(systems),
                       "single_psu": sum(1 for i in items if i["psu"] == "single"),
                       "psu_unknown": sum(1 for i in items if i["psu"] == "unknown"),
                       "no_phone": sum(1 for x in systems if not x["oncall_phone"])},
            "scope_text": scope_text(world, s)}


def _brief(world: World, m: dict) -> dict:
    import blast_radius

    p = world.place(m)
    psu = "vm" if p["is_vm"] else ("single" if m.get("_psu") == 1 else "unknown" if m.get("_psu") is None else "multi")
    apis = list(m.get("api_ids") or [])
    inferred = None
    if not apis:     # 沿用 4-2 的名稱／IP 比對（推論，要標出來）
        nm = getattr(world, "_nm", None) or blast_radius._NameMatcher(world.conn)
        world._nm = nm
        hit = nm.find(m["name"], m["asset_serial"], m.get("ip"))
        if hit and "row" in hit and (hit["row"]["api_id"] or "").strip():
            apis = [hit["row"]["api_id"].strip()]
            inferred = f"{hit['method']}：{hit['row']['hostname']}"
    ip = m.get("ip") or next((x["ip"] for x in m.get("ips", [])), None)
    ppl = world.people_of(m)
    ppl_inferred = None
    if not ppl["ap"] and not ppl["sp"] and inferred:      # 自己沒有登記人：沿用名稱比對到的那筆（標推論）
        nm = world._nm.find(m["name"], m["asset_serial"], m.get("ip"))
        src = (nm or {}).get("row")
        if src is not None:
            ppl = {"ap": [x for x in [(src["user_name"] or "").strip()] if x],
                   "sp": [x for x in [(src["custodian"] or "").strip()] if x]}
            ppl_inferred = inferred
    host = world.host_of(m)
    return {"key": m["key"], "asset_serial": m["asset_serial"], "name": m["name"], "ip": ip,
            "ips": m.get("ips", []), "model": m.get("model"), "machine_type": m.get("machine_type"),
            "machine_type_label": m.get("machine_type_label"),
            "room": p["room"], "floor": p["floor"], "row": p["row"], "rack": p["rack"],
            "u": (f"{m['u_start']}U" if m.get("u_start") == m.get("u_end") else f"{m['u_start']}-{m['u_end']}U")
            if m.get("u_start") else None,
            "esxi": world.label(p["esxi"]) if p["esxi"] else None, "is_vm": p["is_vm"],
            "api_ids": apis, "api_inferred": inferred, "sys_level": m.get("sys_level"),
            "psu": psu, "psu_label": {"vm": "VM", "single": "單電源", "multi": "雙電源以上", "unknown": "不明"}[psu],
            "ap": ppl["ap"], "sp": ppl["sp"], "people_inferred": ppl_inferred,
            "people_garbled": [x for x in ppl["ap"] + ppl["sp"] if is_garbled_name(x)],
            "host_key": host["key"] if host else None, "host_name": host["name"] if host else None,
            "host_serial": host["asset_serial"] if host else None,
            "host_u": (f"{host['u_start']}U" if host.get("u_start") == host.get("u_end") else f"{host['u_start']}-{host['u_end']}U")
            if host and host.get("u_start") else None}


# 人名欄位的亂碼（CIA 匯入時欄位錯位，例如「王小明+V4:xxx」「AB:CD:EF」）：不猜、標出來請 CIA 修
_RE_GARBLED = re.compile(r"[:+;=<>{}\[\]\\|#@$%^*~`]|\bV\d+\b|^[\d\W_]+$")


def is_garbled_name(v: str | None) -> bool:
    v = (v or "").strip()
    return bool(v) and (bool(_RE_GARBLED.search(v)) or len(v) > 30)


_LEVEL_RANK = {"core": 0, "noncore": 1, "unclassified": 2}


def _prio_key(i: dict) -> tuple:
    return (_LEVEL_RANK.get(i.get("sys_level") or "unclassified", 2), 0 if i["psu"] == "single" else 1, i["name"].lower())


def _systems(world: World, items: list[dict]) -> list[dict]:
    import blast_radius
    import rack_layout as rl

    conn = world.conn
    apis = sorted({a for i in items for a in i["api_ids"]})
    levels = {r["api_id"]: rl.sys_class_level(r["sys_class_override"] or r["sys_class"])
              for r in conn.execute("SELECT api_id, sys_class, sys_class_override FROM business_system")}
    oncall = {r["api_id"]: r["phone"] for r in conn.execute("SELECT api_id, phone FROM system_oncall")}
    out = []
    for a in apis:
        mine = [i for i in items if a in i["api_ids"]]
        out.append({"api_id": a, "name": blast_radius._system_name(conn, a), "level": levels.get(a, "unclassified"),
                    "level_label": rl.SYS_LEVEL_LABEL[levels.get(a, "unclassified")],
                    "machines": [i["key"] for i in mine], "machine_names": [i["name"] for i in mine],
                    "oncall_phone": (oncall.get(a) or "").strip() or None,
                    "contacts": blast_radius._system_contacts(conn, a)})
    out.sort(key=lambda x: (_LEVEL_RANK[x["level"]], x["api_id"]))
    return out


def _team_for(i: dict) -> str:
    return {"network": "網管", "san": "儲存", "storage": "儲存"}.get(i.get("machine_type") or "", "主機")


def _sync_tasks(conn, eid: int, exp: dict) -> None:
    """檢查清單：每個系統一列（AP），沒有系統的機器一列（依機器類型給組別）。已存在的不動（保留狀態）。"""
    now = _now()
    for s in exp["systems"]:
        conn.execute("INSERT OR IGNORE INTO incident_task (event_id,level,ref,name,team,status,updated_at) "
                     "VALUES (?,?,?,?,?,'未聯絡',?)", (eid, "system", s["api_id"], f"{s['api_id']} {s['name']}", "AP", now))
    for i in exp["machines"]:
        if not i["api_ids"]:
            conn.execute("INSERT OR IGNORE INTO incident_task (event_id,level,ref,name,team,status,updated_at) "
                         "VALUES (?,?,?,?,?,'未聯絡',?)", (eid, "machine", i["key"], i["name"], _team_for(i), now))


def event_view(conn, eid: int) -> dict:
    """事件頁一次要的東西：事件、範圍展開、最近一輪測試、檢查清單、「先處理這些」。"""
    ev = get_event(conn, eid)
    world = World(conn)
    exp = expand(world, ev["scope"])
    run = last_run(conn, eid)
    res = {x["key"]: x for x in (run or {}).get("items", [])}
    for i in exp["machines"]:
        r = res.get(i["key"])
        i["check"] = r["result"] if r else None
        i["check_label"] = RESULT_LABEL.get(r["result"]) if r else "未測"
    tasks = list_tasks(conn, eid)
    first = []
    for i in exp["machines"]:
        why = []
        if i["sys_level"] == "core":
            why.append("核心系統")
        if i["check"] == "no_response":
            why.append("不通")
        if i["psu"] == "single":
            why.append("單電源")
        if why:
            first.append({**i, "why": why})
    first.sort(key=lambda i: (0 if "核心系統" in i["why"] else 1, 0 if "不通" in i["why"] else 1,
                              0 if "單電源" in i["why"] else 1, i["name"].lower()))
    import blast_radius
    cov = blast_radius.coverage(conn)
    return {"event": ev, "expand": exp, "last_run": run, "tasks": tasks, "first": first,
            "vm_export": cov.get("vm_export"), "teams": TEAMS, "statuses": TASK_STATUS, "types": TYPES}


# ===================================================================== 4. 測一輪
MAX_TARGETS = 500


def run_check(conn, eid: int, by: str | None, runner=None, prober=None) -> dict:
    import host_ping

    _writable(conn, eid)
    exp = expand(World(conn), get_event(conn, eid)["scope"])
    targets = [{"ip": i["ip"], "asset_serial": i["key"], "hostname": i["name"]} for i in exp["machines"] if i["ip"]]
    if len(targets) > MAX_TARGETS:
        raise ValueError(f"範圍內有 {len(targets)} 個 IP，超過一次上限 {MAX_TARGETS}，請縮小範圍")
    started = _now()
    kw = {}
    if runner:
        kw["runner"] = runner
    if prober:
        kw["prober"] = prober
    results = host_ping.check_many(targets, workers=40, **kw) if targets else []
    cnt = {"alive": 0, "no_response": 0, "unknown": 0}
    for r in results:
        cnt[r["result"]] = cnt.get(r["result"], 0) + 1
    no_ip = [i for i in exp["machines"] if not i["ip"]]
    cur = conn.execute("INSERT INTO incident_check_run (event_id,started_at,finished_at,total,ok,down,unknown,created_by) "
                       "VALUES (?,?,?,?,?,?,?,?)", (eid, started, _now(), len(results) + len(no_ip), cnt["alive"],
                                                     cnt["no_response"], cnt["unknown"] + len(no_ip), by))
    rid = cur.lastrowid
    for r in results:
        conn.execute("INSERT INTO incident_check_item (run_id,key,name,ip,result,verdict,checked_at) VALUES (?,?,?,?,?,?,?)",
                     (rid, r["asset_serial"], r["hostname"], r["ip"], r["result"], r.get("verdict"), r.get("checked_at")))
    for i in no_ip:
        conn.execute("INSERT INTO incident_check_item (run_id,key,name,ip,result,verdict,checked_at) VALUES (?,?,?,?,?,?,?)",
                     (rid, i["key"], i["name"], None, "unknown", "沒有 IP 可測", started))
    _log(conn, eid, "check", f"測一輪：通 {cnt['alive']}／不通 {cnt['no_response']}／無法測 {cnt['unknown'] + len(no_ip)}", by)
    conn.commit()
    return get_run(conn, rid)


def get_run(conn, rid: int) -> dict | None:
    r = conn.execute("SELECT * FROM incident_check_run WHERE id = ?", (rid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["items"] = [{**dict(x), "result_label": RESULT_LABEL.get(x["result"], x["result"])} for x in conn.execute(
        "SELECT * FROM incident_check_item WHERE run_id = ? ORDER BY id", (rid,))]
    return d


def last_run(conn, eid: int) -> dict | None:
    ensure_tables(conn)
    r = conn.execute("SELECT id FROM incident_check_run WHERE event_id = ? ORDER BY id DESC LIMIT 1", (eid,)).fetchone()
    return get_run(conn, r["id"]) if r else None


def list_runs(conn, eid: int) -> list[dict]:
    ensure_tables(conn)
    return [dict(r) for r in conn.execute("SELECT * FROM incident_check_run WHERE event_id = ? ORDER BY id DESC", (eid,))]


# ===================================================================== 5. 檢查清單
def list_tasks(conn, eid: int) -> list[dict]:
    ensure_tables(conn)
    return [dict(r) for r in conn.execute("SELECT * FROM incident_task WHERE event_id = ? ORDER BY level DESC, id", (eid,))]


def update_task(conn, tid: int, by: str | None, team: str | None = None, status: str | None = None,
                note: str | None = None) -> dict:
    ensure_tables(conn)
    t = conn.execute("SELECT * FROM incident_task WHERE id = ?", (tid,)).fetchone()
    if not t:
        raise LookupError(f"找不到檢查項目 {tid}")
    _writable(conn, t["event_id"])
    if team is not None and team not in TEAMS:
        raise ValueError(f"組別要是 {'／'.join(TEAMS)}")
    if status is not None and status not in TASK_STATUS:
        raise ValueError(f"狀態要是 {'／'.join(TASK_STATUS)}")
    new = {"team": team if team is not None else t["team"], "status": status if status is not None else t["status"],
           "note": note if note is not None else t["note"]}
    conn.execute("UPDATE incident_task SET team=?, status=?, note=?, updated_by=?, updated_at=? WHERE id=?",
                 (new["team"], new["status"], new["note"], by, _now(), tid))
    ch = [f"{k} {t[k] or '—'}→{new[k] or '—'}" for k in ("team", "status") if new[k] != t[k]]
    if note is not None and note != (t["note"] or ""):
        ch.append(f"備註：{note}")
    if ch:
        _log(conn, t["event_id"], "task", f"{t['name']}：{'；'.join(ch)}", by)
    conn.commit()
    return dict(conn.execute("SELECT * FROM incident_task WHERE id = ?", (tid,)).fetchone())


# ===================================================================== 6. 回報文字
def _dur(start: str, now: datetime) -> str:
    try:
        m = int((now - datetime.strptime(start[:19], "%Y-%m-%d %H:%M:%S")).total_seconds() // 60)
    except ValueError:
        return "?"
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"


def report_text(view: dict, now: datetime | None = None, every_min: int = REPORT_EVERY_MIN) -> str:
    """手機聊天群組貼得下的短行。數字全部從同一份 view 算，不另外維護。"""
    now = now or datetime.now()
    ev, exp, tasks, run = view["event"], view["expand"], view["tasks"], view["last_run"]
    by = {s: [t for t in tasks if t["status"] == s] for s in TASK_STATUS}
    lines = [f"{'【演練・非真實事件】' if ev.get('mode') == 'drill' else ''}【{ev['type']}事件】{ev['title']}",
             f"回報 {now:%m/%d %H:%M}",
             f"開始 {ev['started_at'][5:16]}（已 {_dur(ev['started_at'], now)}）",
             f"影響 {exp['counts']['machines']} 台／{exp['counts']['systems']} 系統",
             f"已恢復 {len(by['OK'])}",
             f"檢查中 {len(by['檢查中'])}",
             f"異常 {len(by['異常'])}"]
    lines += [f"・{t['name']}" for t in by["異常"]]
    lines.append(f"未聯絡 {len(by['未聯絡'])}")
    if run:
        lines.append(f"連線測試 {run['started_at'][11:16]}")
        lines.append(f"通 {run['ok']}／不通 {run['down']}／無法測 {run['unknown']}")
        down = [x["name"] for x in run["items"] if x["result"] == "no_response"]
        lines += [f"・不通 {n}" for n in down[:15]]
        if len(down) > 15:
            lines.append(f"・…另 {len(down) - 15} 台")
    if exp["counts"]["single_psu"]:
        lines.append(f"單電源 {exp['counts']['single_psu']} 台")
    lines.append(f"下次回報 {(now + timedelta(minutes=every_min)):%H:%M}")
    return "\n".join(lines)


def make_report(conn, eid: int, by: str | None, every_min: int = REPORT_EVERY_MIN) -> dict:
    view = event_view(conn, eid)
    now = datetime.now()
    text = report_text(view, now, every_min)
    nxt = (now + timedelta(minutes=every_min)).strftime("%Y-%m-%d %H:%M:%S")
    if view["event"]["status"] == "open":
        conn.execute("UPDATE incident_event SET next_report_at = ? WHERE id = ?", (nxt, eid))
        _log(conn, eid, "report", text, by)
        conn.commit()
    return {"text": text, "next_report_at": nxt}


# ===================================================================== 7. 匯出
def export_csv(conn, eid: int) -> str:
    view = event_view(conn, eid)
    ev = view["event"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["事件", ev["id"], ev["type"], ev["title"], ev["started_at"], ev["status"], ev.get("summary") or ""])
    w.writerow([])
    w.writerow(["時間軸"])
    w.writerow(["時間", "類型", "內容", "誰"])
    for t in timeline(conn, eid):
        w.writerow([t["at"], t["kind"], t["text"], t["created_by"] or ""])
    w.writerow([])
    w.writerow(["受影響機器"])
    w.writerow(["項次", "名稱", "IP", "機房", "櫃", "U", "ESXi", "系統", "系統等級", "電源", "最近測試"])
    for n, i in enumerate(view["expand"]["machines"], 1):
        w.writerow([n, i["name"], i["ip"] or "", i["room"] or "", i["rack"] or "", i["u"] or "", i["esxi"] or "",
                    ",".join(i["api_ids"]), i["sys_level"] or "", i["psu_label"], i["check_label"]])
    w.writerow([])
    w.writerow(["檢查清單"])
    w.writerow(["項次", "對象", "組別", "狀態", "備註", "誰改", "何時"])
    for n, t in enumerate(view["tasks"], 1):
        w.writerow([n, t["name"], t["team"] or "", t["status"], t["note"] or "", t["updated_by"] or "", t["updated_at"] or ""])
    return "﻿" + buf.getvalue()
