"""「進不去的機器」是什麼機器（2026-10-19）。

## 這支在解什麼

2-1 開始收集時，活著但 22／445／5985 都不通的機器，以前一律落到「Push Agent」頁簽、
叫人「請按取得安裝包」。但 Push Agent 只支援 Linux／Unix（bootstrap_watcher.sh 以
`sudo sh` 執行，沒有 Windows 版；AIX 上也跑不起來，理由見 host_kind_rules.json 的 _aix_why）。
於是只開 3389 的 Windows、只開管理埠的設備，都被叫去裝一個根本裝不上的東西。

所以先判斷「它是什麼機器」，再決定下一步：
  Windows → 請開 WinRM（5985）；AIX → 請開 SSH（22）；設備／VMware → 另有納管方式；
  Linux／Unix → 照舊裝 Push Agent；判斷不出來 → 先確認是什麼機器。

## 證據由強到弱（查不到的不要猜）

    人工指定 > 收集到的 OS > 資產登記（os_class 大類，全站同一支）> 探測／掃描線索

前三種講得出依據 → 「確定」；只有探測／掃描線索 → 「推測」（banner 自報例外：它自己說的，
算確定）；什麼都沒有 → 「不明」。線索互相矛盾（Windows 與非 Windows）時一律降成「推測」，
並把兩邊的依據都寫出來。

規則（哪個埠、哪段 TTL 算什麼）放 host_kind_rules.json，不寫死在這裡。
這支只做比對，不碰網路、不改 DB——輸入全部由呼叫端帶進來，測試直接餵。
"""
from __future__ import annotations

import json
import pathlib
import re

_PATH = pathlib.Path(__file__).with_name("host_kind_rules.json")
_RULES: dict | None = None

CONF_CONFIRMED = "confirmed"
CONF_LIKELY = "likely"
CONF_UNKNOWN = "unknown"

KIND_UNKNOWN = "unknown"
KIND_WINDOWS = "windows"

#: 下一步（action）——collect_dispatch 依這個決定狀態與頁簽
ACTION_AGENT = "agent"                    # Linux／Unix：裝 Push Agent
ACTION_AGENT_UNCONFIRMED = "agent_unconfirmed"  # 判斷不出：先確認，安裝包仍給但標未確認
ACTION_WINRM_PORT = "winrm_port"          # Windows：請開 WinRM（5985）
ACTION_SSH_PORT = "ssh_port"              # AIX：請開 SSH（22）
ACTION_NOK = "nok"                        # 設備／VMware／IBM i：另有納管方式


def load_rules() -> dict:
    """讀規則。設定檔壞掉直接丟錯——判斷錯會叫人去做錯的事，寧可整頁報錯被看見。"""
    global _RULES
    if _RULES is None:
        _RULES = json.loads(_PATH.read_text(encoding="utf-8"))
    return _RULES


def reload_rules() -> None:
    global _RULES
    _RULES = None


def kind_label(kind: str) -> str:
    return (load_rules()["kinds"].get(kind) or {}).get("label") or kind


def _family(kind: str) -> str:
    """矛盾只看「Windows 對 非 Windows」——這是決定開 WinRM 還是裝 Agent 的那一刀。
    Linux 對設備（很多設備本身跑 Linux、TTL 也是 64）分不出來，不當矛盾。"""
    return "win" if kind == KIND_WINDOWS else ("none" if kind == KIND_UNKNOWN else "other")


def _ports(v) -> list[int]:
    if not v:
        return []
    if isinstance(v, str):
        v = v.split(",")
    out = []
    for p in v:
        try:
            out.append(int(str(p).strip()))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def _last_octet(ip) -> int | None:
    try:
        parts = str(ip or "").strip().split(".")
        return int(parts[3]) if len(parts) == 4 else None
    except (TypeError, ValueError):
        return None


def _match(rule: dict, ports: set, ttl, banner: str, os_guess: str,
           ip: str | None = None, registered=None) -> str | None:
    """規則全部條件都成立才回證據字串；任一條件缺資料＝不命中（沒有證據就不判）。"""
    fmt = {}
    if "ports_any" in rule:
        hit = sorted(p for p in rule["ports_any"] if p in ports)
        if not hit:
            return None
        fmt["ports"] = "、".join(map(str, hit)) if "ports_none" not in rule             else "、".join(map(str, sorted(ports)))
    if "ports_none" in rule and any(p in ports for p in rule["ports_none"]):
        return None
    if "ttl_min" in rule or "ttl_max" in rule:
        if ttl is None:
            return None
        if not (rule.get("ttl_min", 0) <= int(ttl) <= rule.get("ttl_max", 255)):
            return None
        fmt["ttl"] = int(ttl)
    if "banner_regex" in rule:
        if not banner or not re.search(rule["banner_regex"], banner, re.I):
            return None
        fmt["banner"] = banner[:60]
    if "os_guess_regex" in rule:
        if not os_guess or not re.search(rule["os_guess_regex"], os_guess, re.I):
            return None
        fmt["os_guess"] = os_guess[:60]
    # 2026-10-19（v1.468.3）：資產庫沒登記＋位址是網段頭尾（.1／.254）——閘道／路由器介面／HSRP-VRRP
    # 虛擬 IP 的典型樣子。registered 沒帶（None）＝不知道有沒有登記＝不命中（查不到的不要猜）。
    if rule.get("unregistered"):
        if registered is None or registered:
            return None
        fmt["registered"] = "資產庫沒登記"
    if "ip_last_octet_in" in rule:
        oc = _last_octet(ip)
        if oc is None or oc not in rule["ip_last_octet_in"]:
            return None
        fmt["ip"] = ip
    if not fmt:
        return None          # 沒有任何條件的規則不算（防設定檔寫壞變成全部命中）
    try:
        return rule.get("evidence", "").format(**fmt) or rule["kind"]
    except (KeyError, IndexError):
        return rule.get("evidence") or rule["kind"]


def scan_matches(open_ports=None, ttl=None, banner=None, os_guess=None, *,
                 ip=None, registered=None) -> list[tuple[str, str, str, bool]]:
    """探測／掃描線索命中的規則，照設定檔順序：[(kind, confidence, 證據, 只能佐證)]。

    「只能佐證」（corroborate_only）的規則自己不下判斷——例如 TTL 64：Linux 是 64，
    很多網路設備、ESXi 也是 64，光看它就說 Linux 會把閘道叫去裝 Push Agent（2026-10-19 公司機）。
    """
    ports = set(_ports(open_ports))
    out = []
    for rule in load_rules().get("scan_rules", []):
        ev = _match(rule, ports, ttl, banner or "", os_guess or "", ip, registered)
        if ev:
            out.append((rule["kind"], rule.get("confidence", CONF_LIKELY), ev,
                        bool(rule.get("corroborate_only"))))
    return out


def infer(*, override_kind: str | None = None, override_why: str | None = None,
          collected: str | None = None, registry_class: str | None = None,
          registry_text: str | None = None, open_ports=None, ttl=None,
          banner: str | None = None, os_guess: str | None = None,
          ip: str | None = None, registered=None) -> dict:
    """判斷這台是什麼機器。回 {kind, label, confidence, confidence_label, evidence[], action, nok_kind,
    basis, next_step}。

    - override_kind：人工指定（已換算成本表的 kind）
    - collected：收集到的平台（aix／linux／windows，collected_os.platform_map）
    - registry_class：資產登記的 OS 大類（os_class），registry_text：給人看的登記原文
    - open_ports／ttl／banner／os_guess：探測當下＋上次網段掃描的線索
    - ip／registered：位址與「資產庫有沒有登記」（閘道位址規則用；registered=None＝不知道，不命中）

    只能佐證（corroborate_only）的線索自己不下判斷：只有它們時結論是「不明」，依據照樣列出來。
    """
    cfg = load_rules()
    kinds = cfg["kinds"]
    all_matches = scan_matches(open_ports, ttl, banner, os_guess, ip=ip, registered=registered)
    matches = [(k, c, e) for k, c, e, weak in all_matches if not weak]
    weak = [(k, c, e) for k, c, e, w in all_matches if w]

    strong = None
    if override_kind and override_kind in kinds:
        strong = (override_kind, f"人工指定{('：' + override_why) if override_why else ''}")
    elif collected and cfg.get("collected_kind", {}).get(collected):
        strong = (cfg["collected_kind"][collected], f"收集到的作業系統是 {collected}")
    elif registry_class and cfg.get("os_class_kind", {}).get(registry_class):
        strong = (cfg["os_class_kind"][registry_class],
                  f"資產登記：{registry_text or registry_class}（大類 {registry_class}）")

    evidence: list[str] = []
    if strong:
        basis = "override" if override_kind and override_kind in kinds else (
            "collected" if collected and cfg.get("collected_kind", {}).get(collected) else "registry")
        kind, conf = strong[0], CONF_CONFIRMED
        evidence.append(strong[1])
        for mk, _mc, mev in matches + weak:
            if _family(mk) != _family(kind):
                conf = CONF_LIKELY
                evidence.append(f"但探測線索指向 {kinds[mk]['label']}：{mev}")
            elif mk == kind:
                evidence.append(mev)
    elif matches:
        basis = "scan"
        kind, conf, first = matches[0]
        evidence.append(first)
        for mk, _mc, mev in matches[1:] + weak:
            if _family(mk) != _family(kind):
                conf = CONF_LIKELY
                evidence.append(f"但另有線索指向 {kinds[mk]['label']}：{mev}")
            elif mk == kind:
                evidence.append(mev)
    else:
        basis = "none"
        kind, conf = KIND_UNKNOWN, CONF_UNKNOWN
        ports = _ports(open_ports)
        if weak:
            evidence.append(
                f"線索不足以判斷：開放埠 {'、'.join(map(str, ports)) or '無'}，資產庫也沒登記作業系統")
            evidence += [f"{mev}——單憑這一項不下判斷" for _mk, _mc, mev in weak]
        else:
            evidence.append(
                f"沒有可用的線索：探測只看到開放埠 {'、'.join(map(str, ports)) or '無'}"
                f"{'' if ttl is None else f'、TTL {ttl}'}，資產庫也沒登記作業系統")

    k = kinds.get(kind) or kinds[KIND_UNKNOWN]
    return {
        "kind": kind,
        "label": k["label"],
        "confidence": conf,
        "confidence_label": cfg["confidence_label"][conf],
        "evidence": evidence,
        "action": k["action"],
        "nok_kind": k.get("nok_kind"),
        "basis": basis,
        "next_step": k.get("next_step"),
    }


# ===== 網段掃描表的「研判」（v1.468.6）：跟上方表同一份判斷 =====
_TTL_ONLY_LINUX = re.compile(r"(?=.*ttl)(?=.*(linux|unix))", re.I)


def display_guess(stored: str | None, *, ip: str | None, registered, ttl=None,
                  open_ports=None) -> str | None:
    """掃描存下來的 os_guess，顯示前用現在的規則校正（不改 DB）。

    - 閘道位址（沒登記＋網段頭尾＋沒開服務埠）→ 「網路設備（閘道位址）」，跟 2-1 上方表同一條規則
    - 舊版只憑 TTL 64 寫的「Linux/Unix（TTL≈64）」→ 依現在的規則重算（TTL 64 單獨不判 Linux）
    - 其餘（banner 自報、埠號推的）照舊
    """
    g = infer(ip=ip, registered=registered, ttl=ttl, open_ports=open_ports)
    if g["kind"] == "gateway":
        return f"{g['label']}（推測：網段頭尾位址、沒開服務埠）"
    if stored and _TTL_ONLY_LINUX.search(stored):
        import fingerprint

        return fingerprint.guess_os(ttl=ttl, open_ports=_ports(open_ports))
    return stored


# ===== 從 DB 撈證據（批次，一個 /24 不要發 254 次查詢）=====

def evidence_inputs(conn, ips) -> dict[str, dict]:
    """這批 IP 的「登記／收集／上次掃描」證據。沒有的欄位就不放（不補空值當證據）。

    登記的挑法跟 collect_dispatch.os_class_by_ip 同一個意思：現役優先、講得出類別的優先；
    退役的舊登記（IP 回收前那台）不拿來講現在這台是什麼。
    """
    import collected_os
    import manage_state
    import os_class

    uniq = [ip for ip in dict.fromkeys(ips) if ip]
    out: dict[str, dict] = {ip: {} for ip in uniq}
    if not uniq:
        return out
    collected = collected_os.platform_map(conn)
    rows_by_ip: dict[str, list] = {}
    for i in range(0, len(uniq), 400):
        chunk = uniq[i:i + 400]
        qs = ",".join("?" * len(chunk))
        for r in conn.execute(
                "SELECT ip, asset_serial, os, device_model, group_name, asset_purpose, "
                "asset_status, rowid AS rid FROM hardware "
                f"WHERE ip IN ({qs}) ORDER BY rowid", chunk):
            rows_by_ip.setdefault(r["ip"], []).append(r)
        try:
            for r in conn.execute(
                    "SELECT ip, open_ports, ttl, os_guess FROM scan_history "
                    f"WHERE ip IN ({qs}) AND scan_ok = 1 ORDER BY scan_time DESC, id DESC", chunk):
                d = out[r["ip"]]
                if "scan" not in d:
                    d["scan"] = {"open_ports": r["open_ports"], "ttl": r["ttl"],
                                 "os_guess": r["os_guess"]}
        except Exception:  # noqa: BLE001 - 舊庫沒有掃描指紋欄：少一種線索，不擋判斷
            pass
    for ip, rows in rows_by_ip.items():
        live = [r for r in rows
                if (r["asset_status"] or "").strip() not in manage_state.RETIRED_STATUS] or rows
        d = out[ip]
        for r in live:
            if "collected" not in d and collected.get(r["asset_serial"]):
                d["collected"] = collected[r["asset_serial"]]
            cls = os_class.classify_row(r)
            if "registry_class" not in d and cls not in ("其他", "未知"):
                d["registry_class"] = cls
                d["registry_text"] = " ".join(
                    x for x in (r["os"], r["device_model"]) if x) or r["group_name"] or cls
    return out


def infer_from(inputs: dict | None, *, open_ports=None, ttl=None,
               override_kind: str | None = None, override_why: str | None = None,
               ip: str | None = None, registered=None) -> dict:
    """evidence_inputs 的一筆 ＋ 這次探測的線索 → infer。探測當下的 TTL 優先於上次掃描的。"""
    d = inputs or {}
    scan = d.get("scan") or {}
    ports = sorted(set(_ports(open_ports)) | set(_ports(scan.get("open_ports"))))
    return infer(override_kind=override_kind, override_why=override_why,
                 collected=d.get("collected"), registry_class=d.get("registry_class"),
                 registry_text=d.get("registry_text"), open_ports=ports,
                 ttl=ttl if ttl is not None else scan.get("ttl"),
                 os_guess=scan.get("os_guess"), ip=ip, registered=registered)


# ===== 廠牌（2026-10-19 使用者：「網路設備要寫廠牌」）=====
# 公司機 2-1「網路設備」頁簽是 0、「另有納管方式」卡片寫「設備 5」：網路設備要認得出才進網路設備
# 頁簽，認不出的掉進泛用的「設備」，畫面只寫「設備」等於什麼都沒講。使用者要看得到廠牌。
#
# 證據由強到弱（口徑跟上面「是什麼機器」一樣：確定／推測／不明）：
#   SSH banner 自報產品＝確定 > 資產登記（作業系統／設備機型／資產名稱）的廠牌字樣＝確定
#   > MAC 廠商（OUI）＝推測（Cisco／HPE／Dell 也做伺服器）> 都沒有＝「廠牌不明」
# 規則在 host_kind_rules.json 的 vendor_rules。這支只比對字串，不碰網路、不改 DB、
# **不決定可不可以納管**（呼叫端只對已經是另有納管方式的列加註，見 collect_dispatch._attach_vendor）。

def vendor_from_text(text) -> tuple[str, str | None] | None:
    """一段文字裡的廠牌 → (廠牌, 類別 network／storage／mgmt／esxi／None)；認不出回 None。"""
    s = str(text or "")
    if not s.strip():
        return None
    for rule in load_rules().get("vendor_rules", []):
        if re.search(rule["regex"], s, re.I):
            return rule["vendor"], rule.get("category")
    return None


def vendor_judge(*, banner_product: str | None = None,
                 registry: list[tuple[str, str | None]] | None = None,
                 mac_vendor: str | None = None) -> dict:
    """廠牌判斷。回 {vendor, category, confidence, confidence_label, evidence[]}；vendor=None＝廠牌不明。

    - banner_product：onboard_eligibility.product_from_banner 的結果（產品自報）
    - registry：[(欄位名, 值)]，資產登記的作業系統／設備機型／資產名稱
    - mac_vendor：MAC 前 3 段查到的廠商（掃描的 mac_vendor 或 fingerprint.oui_vendor）
    """
    cfg = load_rules()
    labels = cfg["confidence_label"]
    found: list[tuple[str, str | None, str, str]] = []   # (vendor, category, confidence, 依據)
    if banner_product:
        hit = vendor_from_text(banner_product)
        v, cat = hit if hit else (banner_product, None)
        found.append((v, cat, CONF_CONFIRMED, f"SSH banner 自報：{banner_product}"))
    for field, val in registry or []:
        hit = vendor_from_text(val)
        if hit:
            found.append((hit[0], hit[1], CONF_CONFIRMED, f"資產登記{field}：{str(val)[:60]}"))
    mac_note = None
    if mac_vendor:
        if re.search(cfg.get("vendor_mac_skip_regex") or "$^", mac_vendor, re.I):
            mac_note = f"MAC 廠商是 {mac_vendor}（虛擬網卡／網卡晶片廠商，講不出設備廠牌，不採用）"
        else:
            hit = vendor_from_text(mac_vendor)
            if hit:
                found.append((hit[0], hit[1], CONF_LIKELY,
                              f"MAC 廠商（OUI）：{mac_vendor}——只能推測，{hit[0]} 不一定只做"
                              f"{'網路設備' if hit[1] == 'network' else '這類設備'}"))
            else:
                mac_note = f"MAC 廠商是 {mac_vendor}（不在廠牌對照表，不採用）"
    if not found:
        ev = ["沒有可用的線索：SSH banner 沒自報產品、資產登記沒寫廠牌、也沒有可用的 MAC 廠商"]
        if mac_note:
            ev.append(mac_note)
        return {"vendor": None, "category": None, "confidence": CONF_UNKNOWN,
                "confidence_label": labels[CONF_UNKNOWN], "evidence": ev}
    v, cat, conf, first = found[0]
    evidence = [first]
    for v2, _c2, _conf2, ev2 in found[1:]:
        if v2 == v:
            evidence.append(ev2)
        else:
            conf = CONF_LIKELY          # 線索互相矛盾：降成推測，兩邊都寫出來
            evidence.append(f"但另有線索指向 {v2}：{ev2}")
    if mac_note:
        evidence.append(mac_note)
    return {"vendor": v, "category": cat, "confidence": conf,
            "confidence_label": labels[conf], "evidence": evidence}


def vendor_inputs(conn, ips) -> dict[str, dict]:
    """這批 IP 的廠牌證據（資產登記＋上次掃描的 MAC 廠商）。沒有的不放（不補空值當證據）。

    登記挑法同 evidence_inputs：現役優先；退役的舊登記不拿來講現在這台。
    """
    import fingerprint
    import manage_state

    uniq = [ip for ip in dict.fromkeys(ips) if ip]
    out: dict[str, dict] = {ip: {} for ip in uniq}
    for i in range(0, len(uniq), 400):
        chunk = uniq[i:i + 400]
        qs = ",".join("?" * len(chunk))
        rows_by_ip: dict[str, list] = {}
        for r in conn.execute(
                "SELECT ip, os, device_model, asset_name, mac, asset_status FROM hardware "
                f"WHERE ip IN ({qs}) ORDER BY rowid", chunk):
            rows_by_ip.setdefault(r["ip"], []).append(r)
        for ip, rows in rows_by_ip.items():
            live = [r for r in rows
                    if (r["asset_status"] or "").strip() not in manage_state.RETIRED_STATUS] or rows
            reg = []
            for r in live:
                for field, key in (("的作業系統", "os"), ("的設備機型", "device_model"),
                                   ("的資產名稱", "asset_name")):
                    if r[key] and (field, r[key]) not in reg:
                        reg.append((field, r[key]))
            out[ip]["registry"] = reg
            macv = next((fingerprint.oui_vendor(r["mac"]) for r in live
                         if fingerprint.oui_vendor(r["mac"])), None)
            if macv:
                out[ip]["mac_vendor"] = macv
        try:
            for r in conn.execute(
                    "SELECT ip, mac, mac_vendor FROM scan_history "
                    f"WHERE ip IN ({qs}) AND scan_ok = 1 ORDER BY scan_time DESC, id DESC", chunk):
                d = out[r["ip"]]
                if "scan_mac" not in d:
                    d["scan_mac"] = True
                    v = r["mac_vendor"] or fingerprint.oui_vendor(r["mac"])
                    if v:
                        d["mac_vendor"] = v      # 掃描當下的 MAC 比登記的新
        except Exception:  # noqa: BLE001 - 舊庫沒有 MAC 欄：少一種線索，不擋判斷
            pass
    return out


def net_vendor_view(conn, ip: str | None) -> dict:
    """資產明細頁「網路設備」主機名後的廠牌圖示用（2026-10-21）：同一套 vendor_judge＋vendor_inputs，
    證據順序照舊（資產登記的廠牌字樣＝確定 > MAC 廠商＝推測）。回 {vendor, confidence_label, evidence[]}；
    vendor=None＝廠牌不知道。證據撈不到也回不知道，不擋整頁。"""
    try:
        d = vendor_inputs(conn, [ip]).get(ip) or {} if ip else {}
        v = vendor_judge(registry=d.get("registry"), mac_vendor=d.get("mac_vendor"))
    except Exception as exc:  # noqa: BLE001 - 廠牌只是圖示，算不出來就講不知道＋原因
        return {"vendor": None, "confidence_label": None, "evidence": [f"廠牌判斷失敗：{str(exc)[:120]}"]}
    return {"vendor": v["vendor"], "confidence_label": v["confidence_label"], "evidence": v["evidence"]}
