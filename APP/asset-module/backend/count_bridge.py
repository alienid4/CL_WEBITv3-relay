"""對帳橋：每一筆現役登記歸到「剛好一種角色」，再從角色推出三個候選頭條（試算）。

為什麼要有（使用者 2026-10-26）：全站數字看起來灌水、互相對不起來——5,919 筆登記、3,299 台、
2,662 台在管、5,150「台實際收集」（其實是有填 OS 的筆數）、3,192 搜不到（多半是掃描沒涵蓋）。
使用者拍板順序：**先做對帳橋給他看分類，看完才決定頭條**。所以這支只「算給人看」，不改任何頭條。

使用者的定義（2026-10-26）：
  - 實體設備＝依類型（伺服器／儲存／網路／其他設備）；PC 級機器要在機櫃上才算，沒有機櫃的桌機筆電是端點。
  - 主機＝OS 實例：實體 OS＋VM＋LPAR。ESXi 另列「虛擬化平台」、不算主機。VIOS 算、但標出來。
  - 管理口＝掛在實體機上的（iLO／ILOM／iDRAC…），永遠不算「台」；找不到本體的另列。
  - EXIST1 不變：存不存在看證據（existence.compute），不看 CIA。

分類順序（先命中先算；每筆只會落在一種角色）：
  0. 人工指定（count_role_override，重匯 CIA 不會蓋掉）
  1. 重複登記：同一台（正典 system_stats.machine_key，不另寫去重）的第二筆以後。
     代表筆＝CIA 優先、再依序號（不改 machine_key，只決定哪一筆當代表）。
  2. 有機櫃（v1.502.2：4-5 機櫃畫面畫在櫃上、在用的機器）→ **箱子歸類完全照 rack_layout.rack_boxes**
     （跟 4-5 同一份 merge_plan＋rack_boxes，不另寫一份「同一個箱子」規則）：
       本體 → 實體設備（依類型；ESXi 另列）；同箱的 LPAR → 分割區 LPAR；同箱元件 → 一體機元件／同一個箱子的元件；
       同箱另一個管理 IP → 重複登記；管理口併進主機 → 管理口（掛在本體）；
       管理口找不到本體 → 管理口（找不到本體），有自己的 U 位，4-5 照算一台，實體設備試算也照算；
       疑似登記錯誤 → 各算一台（跟 4-5 一樣）。
     ⇒ 實體設備「有機櫃」＝4-5 機櫃台數、管理口（掛在本體）＝4-5 併進主機的管理口（verify_numbers 釘住）。
  3. 沒有機櫃 U 位的：管理口（rack_layout.is_mgmt_record 或 os_class「管理介面」，HMC／Console 除外）
     → 管理口（找不到本體）；帳外且存在可信度 < 門檻 → 帳外待確認；ESXi／VM。
  4. 機櫃欄填了但認不出（「M11-M12」「網路櫃」「218號-7F」）＝有機櫃位置 → 實體設備（依類型），
     標「機櫃欄認不出」；4-5 列在「無機櫃資料」、寫同一個原因。
  5. 沒有機櫃（v1.502.1 更正：「有機櫃位置的都算」只決定 PC 級機器；伺服器／儲存／網路照類型算實體設備，
     另標「缺機櫃資料」）：
       - 其他來源有 VM 訊號（同一台另一筆標 VM、vCenter 清單有它、VMware MAC…）→ 虛擬機 VM
       - 桌機筆電 → 端點
       - 依類型 → 實體設備（缺機櫃資料）；CIA 的 Windows／Linux 沒標 VM＝實體伺服器
       - 帳外（DY）的 Windows／Linux 沒有任何 VM 訊號、型號也不是實體機 → 無法判斷（判斷不出虛實）

加總＝下鑽：summary 的每一格都是 items 篩出來的筆數（verify_numbers 釘住）。
"""
from __future__ import annotations

import ipaddress
import json
import re
from datetime import datetime
from pathlib import Path

import fleet_cache as _fleet_cache

RULES_PATH = Path(__file__).with_name("count_bridge_rules.json")

#: (key, 標籤, 單位, 單位怎麼算)。順序＝畫面順序＝項次的預設排序。
#: units：rows＝每筆一個；frames＝同框算一個；none＝不算（重複登記）
ROLES = (
    ("phys_server", "實體設備-伺服器", "台", "rows"),
    ("phys_storage", "實體設備-儲存", "台", "rows"),
    ("phys_network", "實體設備-網路", "台", "rows"),
    ("phys_other", "實體設備-其他（有機櫃的 PC 等）", "台", "rows"),
    ("lpar", "分割區 LPAR（4-5 同一個箱子，Power／AS400）", "部", "rows"),
    ("appliance", "一體機元件（Exadata／ODA 等）", "台", "frames"),
    ("frame_multi", "同一個箱子的元件（4-5 推定同一台，非一體機）", "框", "frames"),
    ("frame_suspect", "疑似登記錯誤（同一段 1–2U 擠 4 筆以上，4-5 各算一台）", "台", "rows"),
    ("mgmt_attached", "管理口（掛在本體）", "個", "rows"),
    ("mgmt_orphan", "管理口（找不到本體）", "個", "rows"),
    ("vm", "虛擬機 VM", "部", "rows"),
    ("esxi", "虛擬化平台 ESXi", "台", "rows"),
    ("duplicate", "重複登記（同一台的第二筆以後）", "—", "none"),
    ("endpoint", "端點（桌機筆電，無機櫃）", "台", "rows"),
    ("offbook_pending", "帳外待確認（存在可信度未達門檻）", "筆", "rows"),
    ("unknown", "無法判斷", "筆", "rows"),
)
ROLE_KEYS = tuple(k for k, *_ in ROLES)
ROLE_LABEL = {k: lab for k, lab, *_ in ROLES}
ROLE_UNIT = {k: u for k, _, u, _ in ROLES}
ROLE_UNITS_BY = {k: by for k, _, _, by in ROLES}
_ROLE_ORDER = {k: i for i, k in enumerate(ROLE_KEYS)}

#: 三個候選頭條（試算）：每一項＝某個角色的某種量（下鑽同一份 items）
TRIAL = (
    ("physical", "實體設備", "台", (
        ("phys_server", "units"), ("phys_storage", "units"), ("phys_network", "units"),
        ("phys_other", "units"), ("lpar", "frames"), ("appliance", "frames"),
        ("frame_multi", "frames"), ("frame_suspect", "rows"), ("esxi", "units"), ("mgmt_orphan", "rack"))),
    ("hosts", "主機", "部", (("phys_server", "units"), ("lpar", "rows"), ("vm", "rows"),
                             ("appliance", "os"), ("frame_suspect", "os"))),
    ("mgmt", "管理口", "個", (("mgmt_attached", "rows"), ("mgmt_orphan", "rows"))),
)
MEASURE_LABEL = {"units": "", "rows": "筆數", "frames": "框數", "rack": "有機櫃的（4-5 照算一台）",
                 "os": "跑 OS 的（儲存 cell 除外）"}
#: 「跑 OS」的 OS 大類（L2：一體機元件、疑似登記錯誤裡是這幾類的算主機）
_OS_CLASSES = ("Windows", "Linux", "AIX", "IBM i")
#: 算實體設備的角色（「機櫃欄認不出／缺機櫃資料」子旗標只在這幾種、以及有 U 位的管理口有意義）
PHYS_ROLES = ("phys_server", "phys_storage", "phys_network", "phys_other", "lpar", "appliance",
              "frame_multi", "frame_suspect", "esxi")

_SERVER_CLASSES = ("Windows", "Linux", "AIX", "IBM i", "虛擬化平台")

_RULES: dict | None = None


def load_rules() -> dict:
    global _RULES
    if _RULES is None:
        _RULES = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    return _RULES


def _rx(rules: dict, key: str):
    return re.compile(rules[key], re.I)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 人工指定
def ensure_tables(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS count_role_override ("
        " asset_serial TEXT PRIMARY KEY, role TEXT NOT NULL, note TEXT,"
        " updated_by TEXT, updated_at TEXT NOT NULL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS count_role_override_audit ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, asset_serial TEXT NOT NULL, old_role TEXT,"
        " new_role TEXT, auto_role TEXT, note TEXT, by_user TEXT, at TEXT NOT NULL)")


def overrides(conn) -> dict[str, dict]:
    """有效的人工指定。角色已經不存在（改版拿掉的角色）的不套用，見 invalid_overrides。"""
    ensure_tables(conn)
    return {r["asset_serial"]: dict(r) for r in conn.execute("SELECT * FROM count_role_override")
            if r["role"] in ROLE_KEYS}


def invalid_overrides(conn) -> list[dict]:
    """失效覆寫（2026-10-26 審查 L3）：存的角色現在已經沒有了 → 當作沒指定（回到自動判斷），列出來請人重指定。
    不可以因為一筆舊資料讓整頁 500。"""
    ensure_tables(conn)
    return [dict(r) for r in conn.execute("SELECT * FROM count_role_override ORDER BY asset_serial")
            if r["role"] not in ROLE_KEYS]


def set_override(conn, asset_serial: str, role: str | None, note: str | None, by: str | None) -> dict:
    """人工指定一筆的角色（role=None＝清除，回到自動判斷）。每次都寫稽核紀錄。"""
    ensure_tables(conn)
    serial = (asset_serial or "").strip()
    if not serial:
        raise ValueError("缺資產序號")
    if role is not None and role not in ROLE_KEYS:
        raise ValueError(f"不認得的角色：{role}")
    r = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (serial,)).fetchone()
    if not r:
        raise LookupError(f"找不到資產 {serial}")
    old = conn.execute("SELECT role FROM count_role_override WHERE asset_serial = ?", (serial,)).fetchone()
    auto = next((it["auto_role"] for it in compute(conn, skip_override=True)["items"]
                 if it["asset_serial"] == serial), None)
    if role is None:
        conn.execute("DELETE FROM count_role_override WHERE asset_serial = ?", (serial,))
    else:
        conn.execute(
            "INSERT INTO count_role_override (asset_serial, role, note, updated_by, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(asset_serial) DO UPDATE SET role=excluded.role, note=excluded.note, "
            "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (serial, role, (note or "").strip() or None, by, _now()))
    conn.execute(
        "INSERT INTO count_role_override_audit (asset_serial, old_role, new_role, auto_role, note, by_user, at) "
        "VALUES (?,?,?,?,?,?,?)",
        (serial, old["role"] if old else None, role, auto, (note or "").strip() or None, by, _now()))
    conn.commit()
    return {"asset_serial": serial, "role": role, "auto_role": auto}


def override_audit(conn, limit: int = 200) -> list[dict]:
    ensure_tables(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM count_role_override_audit ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 1000)),))]


# ---------------------------------------------------------------- 分類
def _model(r) -> str:
    return " ".join(str(r["device_model"] or "").split())


def _is_endpoint(r, rules) -> bool:
    return bool(_rx(rules, "endpoint_os_regex").search(r["os"] or "")
                or _rx(rules, "endpoint_model_regex").search(_model(r))
                or _rx(rules, "endpoint_group_regex").search(r["group_name"] or ""))


def _phys_category(r, oc: str, rules) -> tuple[str, str]:
    """單獨一筆有機櫃的實體設備是哪一類（伺服器／儲存／網路／其他）。"""
    import rack_layout

    if _is_endpoint(r, rules):
        return "phys_other", "有機櫃的 PC／端點類"
    if oc == "儲存設備":
        return "phys_storage", "OS 大類：儲存設備"
    if oc == "網路設備":
        return "phys_network", "OS 大類：網路設備"
    if oc in _SERVER_CLASSES:
        return "phys_server", f"OS 大類：{oc}"
    if oc == "管理介面":
        return "phys_other", "管理類實體設備（HMC／Serial Console）"
    t = rack_layout.classify_machine_type(_model(r), [r["os"] or ""])
    if t in ("san", "storage"):
        return "phys_storage", f"型號判斷：{rack_layout.MACHINE_TYPE_LABEL[t]}"
    if t == "network":
        return "phys_network", "型號判斷：網路設備"
    if t in ("linux", "windows", "power", "oracle", "esxi"):
        return "phys_server", f"型號判斷：{rack_layout.MACHINE_TYPE_LABEL[t]}"
    return "phys_other", f"OS 大類：{oc}，型號認不出類別"


def _norack_category(r, oc: str, off: bool, rules) -> tuple[str, str]:
    """沒有機櫃位置、也沒有任何 VM 訊號的代表筆：依類型判實體設備（缺機櫃資料），判不出才無法判斷。

    使用者規則（2026-10-26 更正）：「有機櫃位置的都算」只決定 PC 級機器算不算；伺服器／儲存／網路設備
    機櫃欄沒填（機櫃資料只填了約六成五）照樣是實體設備，另標「缺機櫃資料」。"""
    import rack_layout

    model = _model(r)
    t = rack_layout.classify_machine_type(model, [r["os"] or ""]) if model else "unknown"
    if oc == "儲存設備" or t in ("san", "storage"):
        return "phys_storage", "儲存設備；缺機櫃資料"
    if oc == "網路設備" or t == "network":
        return "phys_network", "網路設備；缺機櫃資料"
    if oc == "管理介面":
        return "phys_other", "管理類實體設備（HMC／Serial Console）；缺機櫃資料"
    if oc in ("AIX", "IBM i"):
        return "phys_server", f"OS 大類：{oc}（不會是 VMware VM）；缺機櫃資料"
    if oc in ("Windows", "Linux"):
        if not off:
            return "phys_server", f"CIA 登記、沒標 VM、也沒有 VM 訊號（OS 大類：{oc}）；缺機櫃資料"
        if model and (t in ("linux", "windows", "power", "oracle") or _rx(rules, "appliance_model_regex").search(model)
                      or re.search(r"\b(dl|ml|r)\d{3}|proliant|poweredge|thinksystem|x3\d{3}|server", model, re.I)):
            return "phys_server", f"帳外、型號是實體機（{model}）；缺機櫃資料"
        return "unknown", f"帳外（DY）、沒有機櫃也沒有任何 VM 訊號，判斷不出虛實（OS 大類：{oc}）"
    if not off and model and model.upper() not in ("N/A", "NA", "-"):
        return "phys_other", f"CIA 登記的設備，型號認不出類別（{model}）；缺機櫃資料"
    return "unknown", f"沒有機櫃、沒有 VM 訊號、OS／型號也判不出類別（OS 大類：{oc}）"


class _VmSignals:
    """沒標 VM 的登記，從其他來源找「它是 VM」的證據（只用既有資料、不連線）：
    同一台的其他登記標了 VM／vi_sdk_server 或 vm_uuid 有值／vCenter（RVTools）清單裡有這個主機名或 IP／
    DY 匯入那筆說 is_vm／OS 是 vSphere 客體 OS 寫法／MAC（登記或掃描看到的）是 VMware 的 OUI。"""

    def __init__(self, conn, rules):
        import manage_state as ms

        self.ms = ms
        self.oui = tuple(x.lower() for x in rules.get("vm_mac_oui", ()))
        self.os_re = re.compile(rules["vm_os_regex"], re.I) if rules.get("vm_os_regex") else None
        self.vc_ids: set[int] = set()
        self.vc_names: set[str] = set()
        self.vc_ips: set[str] = set()
        self.dy_vm_ids: set[int] = set()
        try:
            for src, hid, payload in conn.execute(
                    "SELECT source, resolved_hardware_id, payload FROM source_record WHERE source IN ('vcenter', 'dynassets')"):
                try:
                    p = json.loads(payload or "{}")
                except (ValueError, TypeError):
                    p = {}
                if src == "vcenter":
                    if hid:
                        self.vc_ids.add(hid)
                    for f in ("hostname", "vm_name"):
                        n = _short(p.get(f))
                        if n:
                            self.vc_names.add(n)
                    for ip in ms.ips_of(p.get("ip")):
                        self.vc_ips.add(ip)
                elif hid and ms.is_vm_value(p.get("is_vm"), p.get("device_model")):
                    self.dy_vm_ids.add(hid)
        except Exception:  # noqa: BLE001 - 舊庫沒有 source_record：沒有這類訊號，照其他訊號判
            pass
        self.scan_mac: dict[str, str] = {}
        for ip, mac in conn.execute(
                "SELECT ip, mac FROM scan_history WHERE mac IS NOT NULL AND mac != '' ORDER BY scan_time"):
            self.scan_mac[ip] = str(mac).lower()

    def _vm_mac(self, mac) -> bool:
        m = str(mac or "").strip().lower().replace("-", ":")
        return bool(m) and m.startswith(self.oui)

    def find(self, r, mates) -> str | None:
        if any(self.ms.is_vm_value(m["is_vm"], m["device_model"]) for m in mates):
            return "同一台的其他登記標了 VM"
        if (r["vi_sdk_server"] or "").strip() or (r["vm_uuid"] or "").strip():
            return "有 vCenter／VM UUID 欄位"
        if r["id"] in self.vc_ids or (_short(r["hostname"]) in self.vc_names) \
                or any(ip in self.vc_ips for ip in self.ms.ips_of(r["ip"])):
            return "vCenter（RVTools）清單裡有這個主機名或 IP"
        if r["id"] in self.dy_vm_ids:
            return "DY 匯入標了 VM"
        if self.os_re and self.os_re.search(r["os"] or ""):
            return "OS 字串是 vSphere 客體 OS 的寫法（…(64-bit)）"
        if self._vm_mac(r["mac"]):
            return "MAC 是 VMware 的 OUI"
        if any(self._vm_mac(self.scan_mac.get(ip)) for ip in self.ms.ips_of(r["ip"])):
            return "掃描看到的 MAC 是 VMware 的 OUI"
        return None


def _short(h) -> str:
    import machine_identity

    n = machine_identity.norm_name(h)
    return "" if machine_identity.is_generic(n) or n.upper() in ("N/A", "NA", "-") else n


def _rack_index(conn, ms: list[dict]) -> dict[str, dict]:
    """有機櫃（4-5 認得的機櫃 U 位、在用）的機器 → 4-5 機櫃畫面的歸類。**直接呼叫 rack_layout.rack_boxes**
    （跟 rack_layout.datacenter 同一份 merge_plan＋rack_boxes），不自己再寫一份「同一個箱子」的規則：
    箱子本體／同箱其他登記（LPAR／元件／多管理 IP）／管理口併進主機／找不到本體的管理口／疑似登記錯誤。"""
    import overlap_queue
    import rack_layout

    acts = overlap_queue.active_decisions(conn)
    racks: dict[tuple, list] = {}
    for m in ms:
        if m["kind"] == "rack":
            racks.setdefault((m["room"], m["rack"]), []).append(m)
    out: dict[str, dict] = {}
    for (rm, rk), devs in racks.items():
        plan = overlap_queue.merge_plan(devs, rm, rk, acts)
        bx = rack_layout.rack_boxes(devs, plan)
        for d in devs:
            if d["retired"] or d["state"] != "active":
                continue                       # 4-5 只算在用的；退役（關機未下架／待確認）不在對帳橋裡
            k = d["key"]
            out[k] = {"cat": bx["category"][k], "role": bx["roles"].get(k), "head": bx["head_of"].get(k, k),
                      "mgmt": bx["mgmt"].get(k), "m": d}
    for k, v in out.items():
        h = v["head"]
        v["box_roles"] = {x["role"] for x in out.values() if x["head"] == h and x["cat"] == "member"}
    return out


def compute(conn, skip_override: bool = False, rules: dict | None = None) -> dict:
    """全部現役登記的角色（預設參數走跨請求快取，見 _compute_default）。"""
    if not skip_override and rules is None:
        return _compute_default(conn)
    return _compute(conn, skip_override, rules)


@_fleet_cache.cached   # 跨請求快取：資料庫沒變就不重算（fleet_cache）
def _compute_default(conn) -> dict:
    """2.006.2：首頁、主機履歷、2.1 每次請求都重算一次（約 0.6 秒）。只快取預設參數；算法不變。"""
    return _compute(conn, False, None)


def _compute(conn, skip_override: bool = False, rules: dict | None = None) -> dict:
    """全部現役登記的角色。回 {"items": [...], "summary": {...}}。不改資料。"""
    import existence
    import manage_state as ms
    import os_class
    import rack_layout
    import system_report as sr
    import system_stats as ss

    rules = rules or load_rules()
    box_re = _rx(rules, "mgmt_box_model_regex")
    appliance_re = _rx(rules, "appliance_model_regex")
    cell_re = _rx(rules, "storage_cell_hostname_regex")

    ex = existence.compute(conn)
    thr = int(ex["summary"]["count_threshold"])
    ex_by_key = {m["key"]: m for m in ex["machines"]}
    ovr = {} if skip_override else overrides(conn)
    rms = rack_layout.machines(conn)            # 一台一筆（machine_key），含位置更正後的機房／機櫃／U
    rm_by_key = {m["key"]: m for m in rms}
    rack_ix = _rack_index(conn, rms)

    rows = [r for r in conn.execute("SELECT * FROM hardware ORDER BY asset_serial")
            if (r["asset_status"] or "").strip() not in ms.RETIRED_STATUS]
    info: dict[int, dict] = {}
    for r in rows:
        oc = os_class.classify_row(r)
        mk = ss.lifecycle_prefix(r) + ss.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        e = ex_by_key.get(mk)
        m = rm_by_key.get(mk) or {}
        status = "rack" if mk in rack_ix else ("unparsed" if m.get("kind") == "unparsed" else "none")
        info[r["id"]] = {
            "r": r, "oc": oc, "mk": mk, "m": m, "status": status,
            "off": sr.is_off_book(r["asset_serial"]),
            "score": e["score"] if e else None, "counted": bool(e and e["counted"]),
            "role": None, "reason": "", "frame": None, "host": None,
        }

    def _set(i, role, reason, **kw):
        i["role"], i["reason"] = role, reason
        i.update(kw)

    order = lambda i: (i["off"], i["r"]["asset_serial"] or "")  # noqa: E731 - CIA 優先、再依序號

    # ── 同一台（machine_key）：代表筆以外都是重複登記（4-5 也是一台一筆畫）
    # 2026-10-26 審查 M2：**全部**登記一起分組（含人工指定的）。以前先把人工指定的拿掉再分組，
    # 指定了代表筆，同一台的第二筆就被升格成代表——一台變兩台。現在代表筆照舊，人工指定只改那一筆自己的角色。
    groups: dict[str, list] = {}
    for i in info.values():
        groups.setdefault(i["mk"], []).append(i)
    reps = []
    for g in groups.values():
        g.sort(key=order)
        rep = g[0]
        rep["_mates"] = [x["r"] for x in g[1:]]
        reps.append(rep)
        for i in g[1:]:
            _set(i, "duplicate", f"跟 {rep['r']['asset_serial']} 是同一台（machine_key）")

    sig = _VmSignals(conn, rules)
    for i in reps:
        r, oc, mk = i["r"], i["oc"], i["mk"]
        rx = rack_ix.get(mk)
        if rx:
            # ── 有機櫃（4-5 畫在機櫃上、在用）：箱子歸類完全照 rack_layout.rack_boxes
            cat, head = rx["cat"], rx["head"]
            hm = rack_ix[head]["m"] if head in rack_ix else rx["m"]
            if cat == "port":
                _set(i, "mgmt_attached", f"4-5 併進本體 {hm['name']}（{rx['mgmt']['kind']}，規則 {rx['mgmt'].get('rule')}）",
                     host=hm["asset_serial"])
            elif cat == "orphan":
                _set(i, "mgmt_orphan", "4-5：管理介面・找不到本體（有自己的 U 位，4-5 照算一台）")
            elif cat == "suspect":
                _set(i, "frame_suspect", "4-5：疑似登記錯誤（同一段 1–2U 擠了 4 筆以上，各算一台）")
            else:
                broles = rx["box_roles"]
                if cat == "member" and rx["role"] == "multi_ip":
                    _set(i, "duplicate", f"4-5：跟 {hm['name']} 是同一個箱子的另一個管理 IP", frame=f"box:{head}")
                elif "lpar" in broles or (cat == "member" and rx["role"] == "lpar"):
                    _set(i, "lpar", f"4-5：同一個箱子（{hm['name']}）的分割區 LPAR", frame=f"box:{head}")
                elif "component" in broles or (cat == "member" and rx["role"] == "component"):
                    role = "appliance" if appliance_re.search(hm.get("model") or "") else "frame_multi"
                    _set(i, role, f"4-5：同一個箱子（{hm['name']}）的元件", frame=f"box:{head}")
                elif oc == "虛擬化平台":
                    _set(i, "esxi", "OS 大類：虛擬化平台（4-5 機櫃上的一台）")
                else:
                    role, why = _phys_category(r, oc, rules)
                    if ms.is_vm_value(r["is_vm"], r["device_model"]):
                        why += "；登記標了 VM 但有機櫃 U 位（4-5 算實體），請確認"
                    _set(i, role, why)
            continue
        vm = ms.is_vm_value(r["is_vm"], r["device_model"]) or i["m"].get("kind") in ("vm", "vm_model")
        # 2026-10-26 審查 M1／再審：強訊號（-ilom 尾巴、OS／型號寫 iDRAC…）一定是管理口；
        # 弱訊號（OOB、遠端管理）跟 os_class「管理介面」要先排除 VM（SECOOB／SVROOB 開頭的 VM 以前被當成管理口）
        sig_m = rack_layout.mgmt_signal(i["m"]) if i["m"] else None
        mgmt = sig_m == "strong" or (not vm and ((rack_layout.is_mgmt_record(i["m"]) if i["m"] else False)
                                                 or (oc == "管理介面" and not box_re.search(_model(r)))))
        if mgmt:
            _set(i, "mgmt_orphan", "管理口，但沒有機櫃 U 位，跟 4-5 一樣配不到本體")
        elif i["off"] and not i["counted"]:
            _set(i, "offbook_pending", f"帳外登記，存在可信度 {i['score'] if i['score'] is not None else '—'} < {thr}")
        elif oc == "虛擬化平台" and not vm:
            # 2026-10-27 決定（v2.007.6，取代 v2.007.5）：VMware 平台（ESXi／vCenter）有明確 VM 證據（型號／機櫃欄「(VM)」、
            # is_vm、vCenter 的 VM 匯入）＝虛擬機（vCenter 一體機本來就是 VM；標 (VM) 的 ESXi 多半是巢狀 ESXi）；
            # 沒有 VM 證據的才是 ESXi 實體機。天條（不納管、不收集）看的是作業系統大類，不受角色影響。
            _set(i, "esxi", "OS 大類：虛擬化平台；" + ("機櫃欄認不出" if i["status"] == "unparsed" else "缺機櫃資料"))
        elif vm:
            _set(i, "vm", "is_vm／型號（VM）／機櫃欄（VM）")
        elif i["status"] == "unparsed":
            role, why = _phys_category(r, oc, rules)
            _set(i, role, f"{why}；機櫃欄認不出（{i['m'].get('rack_raw')}）")
        elif (why := sig.find(r, i.get("_mates", []))):
            _set(i, "vm", f"VM 訊號：{why}")
        elif _is_endpoint(r, rules):
            _set(i, "endpoint", "沒有機櫃位置的桌機筆電（OS／型號／群組）")
        else:
            role, why = _norack_category(r, oc, i["off"], rules)
            _set(i, role, why)

    # ── 人工指定
    for i in info.values():
        o = ovr.get(i["r"]["asset_serial"])
        i["auto_role"] = i["role"]
        if o:
            i["auto_role"] = None
            _set(i, o["role"], f"人工指定：{o['note'] or '（沒寫原因）'}（{o['updated_by'] or '—'} {o['updated_at']}）")

    items = []
    for i in info.values():
        r, m = i["r"], i["m"]
        racked = i["status"] == "rack"
        u = None
        if racked and m.get("u_start"):
            u = f"{m['u_start']}" if m["u_end"] in (None, m["u_start"]) else f"{m['u_start']}-{m['u_end']}"
        phys = i["role"] in PHYS_ROLES or (i["role"] == "mgmt_orphan" and racked)
        items.append({
            "asset_serial": r["asset_serial"], "side": "帳外" if i["off"] else "CIA",
            "hostname": r["hostname"], "ip": r["ip"], "os": r["os"], "os_class": i["oc"],
            "model": _model(r) or None, "room": m.get("room") or None,
            "rack": m.get("rack") if racked else None, "u": u, "rack_raw": m.get("rack_raw") or r["rack_no"],
            "rack_status": i["status"], "racked": racked,
            "rack_unparsed": phys and i["status"] == "unparsed",
            "rack_missing": phys and i["status"] == "none",
            "role": i["role"], "role_label": ROLE_LABEL[i["role"]],
            "auto_role": i["auto_role"], "override": i["auto_role"] is None,
            "reason": i["reason"], "frame": i["frame"] or (f"solo:{r['asset_serial']}"),
            "host_serial": i["host"], "machine_key": i["mk"],
            "exist_score": i["score"], "counted": i["counted"],
            "vios": "vios" in f"{r['os'] or ''} {r['hostname'] or ''}".lower(),
            # 2026-10-26 使用者決定（L2）：一體機元件裡跑 OS 的（Exadata／ODA 的 DB 節點，Linux）算主機，儲存 cell 不算；
            # 疑似登記錯誤裡是伺服器 OS 的也算主機
            "os_instance": i["role"] in ("appliance", "frame_suspect") and i["oc"] in _OS_CLASSES
            and not (i["role"] == "appliance" and cell_re.search(r["hostname"] or "")),
        })
    items.sort(key=lambda x: (_ROLE_ORDER[x["role"]], x["side"] != "CIA", x["room"] or "", x["rack"] or "",
                              x["u"] or "", (x["hostname"] or "").lower(), x["asset_serial"] or ""))
    for n, it in enumerate(items, 1):
        it["no"] = n
    ex_in_db = sum(1 for m in ex["machines"] if m["in_asset_db"] and not m["retired"] and m["counted"])
    return {"items": items, "summary": summarize(items), "count_threshold": thr,
            "existence_counted_in_db": ex_in_db, "as_of": _now(),
            "invalid_overrides": [] if skip_override else invalid_overrides(conn)}


# ---------------------------------------------------------------- 彙總（全部從 items 算）
RACK_STATUS = ("rack", "unparsed", "none")
RACK_STATUS_LABEL = {"rack": "有機櫃", "unparsed": "機櫃欄認不出", "none": "缺機櫃資料"}


def measure(items: list[dict], role: str, how: str = "units", counted_only: bool = False,
            rack: str | None = None) -> int:
    """某角色的某種量。how：units（照角色的單位）／rows／frames；rack、unparsed、none＝units 只算那種機櫃狀態。
    rack＝另外再篩一種機櫃狀態（rack／unparsed／none）；兩個條件都要成立。"""
    want = {how} if how in RACK_STATUS else set()
    if want:
        how = "units"
    if rack:
        want = want & {rack} if want else {rack}
        if not want:
            return 0
    xs = [x for x in items if x["role"] == role and (x["counted"] or not counted_only)
          and (not want or x["rack_status"] in want)]
    if how == "os":
        return sum(1 for x in xs if x["os_instance"])
    if how == "units":
        how = {"rows": "rows", "frames": "frames", "none": "none"}[ROLE_UNITS_BY[role]]
    if how == "none":
        return 0
    if how == "frames":
        return len({x["frame"] for x in xs})
    return len(xs)


def summarize(items: list[dict]) -> dict:
    roles = []
    for k, lab, unit, _ in ROLES:
        xs = [x for x in items if x["role"] == k]
        roles.append({
            "role": k, "label": lab, "unit": unit,
            "cia_rows": sum(1 for x in xs if x["side"] == "CIA"),
            "offbook_rows": sum(1 for x in xs if x["side"] == "帳外"),
            "rows": len(xs), "units": measure(items, k), "frames": measure(items, k, "frames"),
            "counted_rows": sum(1 for x in xs if x["counted"]),
            "counted_units": measure(items, k, counted_only=True),
            "override_rows": sum(1 for x in xs if x["override"]),
            "vios_rows": sum(1 for x in xs if x["vios"]),
            "racked_rows": sum(1 for x in xs if x["racked"]),
            "unparsed_rows": sum(1 for x in xs if x["rack_unparsed"]),
            "no_rack_rows": sum(1 for x in xs if x["rack_missing"]),
        })
    trial = []
    for key, lab, unit, parts in TRIAL:
        ps = []
        for r, how in parts:
            p = {"role": r, "measure": how,
                 "label": ROLE_LABEL[r] + (f"（{MEASURE_LABEL[how]}）" if MEASURE_LABEL.get(how) else ""),
                 "n": measure(items, r, how), "counted": measure(items, r, how, counted_only=True)}
            for st in RACK_STATUS:
                p[st] = measure(items, r, how, rack=st)
            ps.append(p)
        t = {"key": key, "label": lab, "unit": unit, "parts": ps,
             "value": sum(p["n"] for p in ps), "counted": sum(p["counted"] for p in ps)}
        for st in RACK_STATUS:
            t[st] = sum(p[st] for p in ps)
        trial.append(t)
    lp = [x for x in items if x["role"] == "lpar"]
    return {
        "roles": roles, "trial": trial, "rack_status_label": RACK_STATUS_LABEL,
        "totals": {"rows": len(items), "cia_rows": sum(1 for x in items if x["side"] == "CIA"),
                   "offbook_rows": sum(1 for x in items if x["side"] == "帳外"),
                   "override_rows": sum(1 for x in items if x["override"]),
                   "counted_machines": len({x["machine_key"] for x in items if x["counted"]})},
        "lpar_vios": sum(1 for x in lp if x["vios"]),
    }


def filter_items(items: list[dict], role: str | None = None, side: str | None = None,
                 measure_: str | None = None, counted: bool | None = None) -> list[dict]:
    """下鑽。measure_＝rack／unparsed／none 只看那種機櫃狀態（舊名 racked／no_rack 也收）。"""
    if role and role not in ROLE_KEYS:
        raise ValueError(f"不認得的角色：{role}")
    if side and side not in ("CIA", "帳外"):
        raise ValueError("side 只接受 CIA／帳外")
    out = [x for x in items if (not role or x["role"] == role) and (not side or x["side"] == side)]
    st = {"racked": "rack", "no_rack": "none"}.get(measure_ or "", measure_)
    if st in RACK_STATUS:
        out = [x for x in out if x["rack_status"] == st]
    if counted is not None:
        out = [x for x in out if x["counted"] == counted]
    return out


# ---------------------------------------------------------------- 對帳（只拿這次掃描有涵蓋的網段當分母）
RECON_BUCKETS = ("overlap", "missing", "uncovered")
RECON_LABEL = {"overlap": "一致", "missing": "搜不到（有涵蓋的網段）", "uncovered": "未涵蓋"}


def scan_basis(conn, now=None) -> dict:
    """對帳的「這一輪掃描」（2026-10-26 審查 H1）：每個網段取**這一輪**裡最近一次真的有掃到東西的那次。

    以前用 MAX(scan_time)：221 上最新一次是中午 1 個網段的補掃，於是 3,189 台「未涵蓋」——
    凌晨的全網段排程（172 段）明明掃過。現在：
      - 這一輪＝近 N 天（N＝2-1「幾天算舊」、跟首頁紅燈 scan_scope.recent_coverage 同一個數字）
      - 每個網段取 scan_coverage ok=1 的最新時間，但那次掃描**一定要有存活紀錄**（scan_history 有 scan_ok=1 的列）。
        整輪回 0 台的掃描（掃描器壞掉、網路不通）不能當成「掃過、全部沒回應」，不然整片都會變成「搜不到」。
      - 存活：那個網段那一次掃描的存活紀錄；IP 不在任何涵蓋網段的存活紀錄，用最近一次有結果的掃描。
    回 {"nets": [(network, 時間)], "alive_rows": [...], "latest_full": {...}, "empty_runs": [...], "days", "cutoff"}。"""
    import scan_scope

    cov = scan_scope.recent_coverage(conn, now=now)
    cutoff = cov["cutoff"]
    valid = {r[0] for r in conn.execute("SELECT DISTINCT scan_time FROM scan_history WHERE scan_ok = 1 AND scan_time >= ?",
                                        (cutoff,))}
    per_cidr: dict[str, str] = {}
    by_time: dict[str, set] = {}
    empty_by_time: dict[str, set] = {}
    try:
        for cidr, t in conn.execute("SELECT cidr, scan_time FROM scan_coverage WHERE ok = 1 AND scan_time >= ?", (cutoff,)):
            if t not in valid:
                empty_by_time.setdefault(t, set()).add(cidr)
                continue
            by_time.setdefault(t, set()).add(cidr)
            if t > per_cidr.get(cidr, ""):
                per_cidr[cidr] = t
    except Exception:  # noqa: BLE001 - 舊庫沒有 scan_coverage：只用存活紀錄上記的網段
        pass
    for seg, t in conn.execute("SELECT DISTINCT segment, scan_time FROM scan_history WHERE scan_ok = 1 AND scan_time >= ? "
                               "AND segment IS NOT NULL", (cutoff,)):
        by_time.setdefault(t, set()).add(seg)
        if t > per_cidr.get(seg, ""):
            per_cidr[seg] = t
    # 回 0 台的掃描（2026-10-26 再審）：只掃 1–2 段、一台都沒回＝那一段可能整段斷線 → **照算涵蓋**（那段的機器變「搜不到」），
    # 畫面標紅；一次掃很多段、全部 0 台＝掃描器或網路壞了 → 跳過、用前一次有結果的。門檻在 count_bridge_rules.json。
    # 2026-10-26 決定：小的 0 台補掃，只有在**前後 24 小時內掃描器確實有掃到東西**時才算「可能整段斷線」；
    # 否則是掃描器那段時間整體沒回應（跟大的 0 台排程同一場故障），一樣略過、列在黃色警示。
    small_max = int(load_rules().get("empty_run_small_max_segments", 2))
    win_h = float(load_rules().get("empty_run_alive_window_hours", 24))

    def _dt(s):
        try:
            return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    valid_dt = [d for d in (_dt(v) for v in valid) if d]
    big_empty_dt = [d for d in (_dt(t) for t, cs in empty_by_time.items() if len(cs) > small_max) if d]

    def _scanner_ok_near(t) -> bool:
        """前後 win_h 小時內有掃到東西的掃描，而且兩者之間沒有夾著一次「多段全部 0 台」的掃描
        （221：10-22 13:22 有結果、10-23 01:15 全網段 0 台、10-23 13:20 補掃 0 台——中間那次已經證明掃描器壞了）。"""
        d = _dt(t)
        if not d:
            return False
        for v in valid_dt:
            if abs((v - d).total_seconds()) <= win_h * 3600:
                lo, hi = min(v, d), max(v, d)
                if not any(lo < b < hi for b in big_empty_dt):
                    return True
        return False
    empty_runs = []
    down: dict[str, str] = {}
    for t, cidrs in sorted(empty_by_time.items(), reverse=True):
        small = len(cidrs) <= small_max and _scanner_ok_near(t)
        kind = "suspect_down" if small else ("skipped_outage" if len(cidrs) <= small_max else "skipped")
        empty_runs.append({"scan_time": t, "segments": len(cidrs), "kind": kind,
                           "cidrs": sorted(cidrs) if len(cidrs) <= small_max else []})
        if small:
            for c in cidrs:
                if t > per_cidr.get(c, ""):
                    per_cidr[c] = t
                    down[c] = t
    nets = []
    for c, t in sorted(per_cidr.items()):
        try:
            nets.append((ipaddress.ip_network(c, strict=False), t))
        except ValueError:
            continue
    latest_valid = max(valid) if valid else None
    # 最近一次「全網段」掃描＝這一輪裡涵蓋網段最多的那次（同樣多取最新）
    lf = max(by_time.items(), key=lambda kv: (len(kv[1]), kv[0])) if by_time else None
    need = sorted({t for _, t in nets} | ({latest_valid} if latest_valid else set()))
    rows = []
    for i in range(0, len(need), 400):
        chunk = need[i:i + 400]
        ph = ",".join("?" for _ in chunk)
        rows += conn.execute(f"SELECT ip, hostname, scan_time FROM scan_history WHERE scan_ok = 1 AND scan_time IN ({ph})",
                             chunk).fetchall()

    def time_for(ip) -> str | None:
        try:
            a = ipaddress.ip_address(ip)
        except (ValueError, TypeError):
            return latest_valid
        best = None
        for n, t in nets:
            if a in n and (best is None or t > best):
                best = t
        return best or latest_valid

    alive = [r for r in rows if r["scan_time"] == time_for(r["ip"])]
    return {"nets": nets, "alive_rows": alive, "days": cov["days"], "cutoff": cutoff,
            "latest_full": {"scan_time": lf[0], "segments": len(lf[1])} if lf else None,
            "empty_runs": empty_runs, "down": down}


def recon_coverage(conn, now=None) -> dict:
    """CIA 現役登記（排退役＋帳外，跟 /api/dashboard/stats 同一個 universe）逐台對「這一輪」掃描（見 scan_basis）。

    舊口徑「搜不到＝登記 − 一致」把這次根本沒掃到的網段也算成搜不到（221：3,192 台裡大多是沒涵蓋）。
    這裡拆成：一致（掃得到）／搜不到（IP 在這一輪有涵蓋的網段裡、卻沒回應）／未涵蓋
    （IP 不在這一輪涵蓋的網段裡，或沒有可掃描的 IP）。三者互斥、加總＝登記台數。"""
    import manage_state as ms
    import system_report as sr
    import system_stats as ss

    basis = scan_basis(conn, now=now)
    sip = {r["ip"] for r in basis["alive_rows"] if r["ip"]}
    shost = {r["hostname"] for r in basis["alive_rows"] if r["hostname"]}
    nets = [n for n, _ in basis["nets"]]

    def _in_nets(ip) -> bool:
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(a in n for n in nets)

    machines: dict[str, dict] = {}
    all_ips, all_hosts = set(), set()
    for r in conn.execute("SELECT asset_serial, hostname, ip, asset_status FROM hardware ORDER BY asset_serial"):
        if r["ip"]:
            all_ips.add(r["ip"])
        if r["hostname"]:
            all_hosts.add(r["hostname"])
        if (r["asset_status"] or "").strip() in ms.RETIRED_STATUS or sr.is_off_book(r["asset_serial"]):
            continue
        k = ss.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        m = machines.setdefault(k, {"key": k, "asset_serial": r["asset_serial"], "hostname": r["hostname"],
                                    "ip": r["ip"], "serials": [], "seen": False, "covered": False})
        m["serials"].append(r["asset_serial"])
        if (r["ip"] and r["ip"] in sip) or (r["hostname"] and r["hostname"] in shost):
            m["seen"] = True
        if any(_in_nets(ip) for ip in ms.ips_of(r["ip"])):
            m["covered"] = True
    items = []
    for m in machines.values():
        b = "overlap" if m["seen"] else ("missing" if m["covered"] else "uncovered")
        items.append({**m, "bucket": b, "bucket_label": RECON_LABEL[b]})
    items.sort(key=lambda x: (RECON_BUCKETS.index(x["bucket"]), (x["hostname"] or "").lower(), x["asset_serial"] or ""))
    for n, it in enumerate(items, 1):
        it["no"] = n
    counts = {b: sum(1 for x in items if x["bucket"] == b) for b in RECON_BUCKETS}
    covered = counts["overlap"] + counts["missing"]
    # 依據日期＝真的有結果的那幾次（不含「可能整段斷線」的 0 台補掃，那幾段另外列在 down）
    down_times = set(basis["down"].values())
    times = sorted({t for _, t in basis["nets"] if t not in down_times})
    # 這一輪掃到、資產庫（含退役／帳外）完全查無的位址（跟首頁「這次掃到、清冊查無」同一條比對，但用同一輪的存活紀錄）
    scan_only = len({(r["ip"], r["hostname"]) for r in basis["alive_rows"]
                     if not ((r["ip"] and r["ip"] in all_ips) or (r["hostname"] and r["hostname"] in all_hosts))})
    return {"scan_time": times[-1] if times else None, "scan_from": times[0] if times else None,
            "scan_only": scan_only,
            "days": basis["days"], "covered_segments": len(nets), "latest_full": basis["latest_full"],
            "empty_runs": basis["empty_runs"],
            "suspect_down": [{"cidr": c, "scan_time": t} for c, t in sorted(basis["down"].items())],
            "total": len(items), "covered": covered, "counts": counts,
            "items": items}


def match_pct_covered(overlap: int, covered: int, scan_only: int) -> float | None:
    """帳實相符（只算有涵蓋的網段）＝一致 ÷（有涵蓋的登記 ∪ 掃到沒登記的）。沒有分母回 None。"""
    den = covered + scan_only
    return round(overlap * 100.0 / den, 1) if den else None
