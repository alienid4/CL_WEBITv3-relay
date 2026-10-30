"""實測主機名校正 → 同一台（v1.476.0，2026-10-20，決策 PRECEDENCE1）。

使用者：「已經納管了，可以抓到最正確的資訊，比 CIA、比 DYN 都準」
「實際登進去收到的資料最準，其次是 RVTools，再來是掃描產生的 DYN，最後才是 CIA」。

公司實例：CIA HW-00010439 寫 SECSVR018-145TT @ 10.x.18.142；我們對 .142 用 WinRM 納管，
機器自己講 SECSVR018-142T（DYN-10.x.18.142）。主機名不同 → 以前算兩台、「同 IP 另 1 筆」。

## 規則

在 IP X 上，取「機器在 X 講的主機名」H（依證據強度取最強的一層，該層只有一個名字才算）：
  1. collect：在 X 登入收集（collect_ok=1）——帳外那筆的 hostname／CIA 那筆的 hostname_observed
  2. rvtools：RVTools vInfo 的 DNS Name（VMware Tools 回報）且 IP＝X——**只認 DNS Name，VM 名不算**（dns_name）
  3. scan：掃描建的帳外那筆（DYN-，沒納管）在 X 的主機名（觀察到的名稱，不是推測）
哪幾層可以拿來校正寫在 correction_fields.json 的 hostname.correct_by。

CIA 登記 C @ X、H≠C：
  - C 在別的 IP Y 還活著（別筆登記主機名＝C，且收集過／近 N 天掃到／RVTools 有）→ 是 **CIA 的 IP 填錯**，
    主機名不校正，列「疑似：CIA IP 填錯（C 實際在 Y）」。（068T 那台：CIA 068T@.168，.168 實測 168T，
    但 068T 在 .68 收集過 → 不校正）
  - 同一個 IP 已經有另一筆 CIA 登記叫 H → C 比較像舊機器或另一筆，不校正、列疑似。
  - 同一個 IP 有兩個以上不同名字的 CIA 登記（VIP／叢集）→ 不校正、列疑似。
  - C 跟 H 不像（相似度 < SIMILAR）→ 比較像 IP 回收給新機器，不校正、列疑似。
  - 否則 C 當成 CIA 打錯字：這筆的「有效主機名」＝H（⟲ 系統校正）。

有效主機名進 machine_key（machine_identity.effective_hostname）：同 IP 的 DYN 空殼跟 CIA 正本變同一台，
部署時 dyn_shell auto-merge 用既有的合併關係併回（CIA 序號當正本；資產狀態不動）。
**CIA 匯入的 hostname 欄不改**。關掉：app_settings hostname_fix_mode=off 或環境變數 WEBIT_HOSTNAME_FIX=off，
machine_key 立刻回到原樣（不改資料，可逆；已併回的空殼走 3-13 還原）。

這支不可以呼叫 machine_key（會自己咬自己）：只用原始主機名正規化比對。
"""
from __future__ import annotations

import hashlib
import json
import os

MODE_SETTING = "hostname_fix_mode"
ENV_OFF = "WEBIT_HOSTNAME_FIX"
ALIVE_DAYS = 30
#: C 跟 H 要像到這個程度才當打錯字（difflib 比例；SECSVR018-145TT vs SECSVR018-142T ≈ 0.9）
SIMILAR = 0.6
TIERS = ("collect", "rvtools", "scan")

_CACHE: dict = {}


def enabled(conn) -> bool:
    if os.environ.get(ENV_OFF, "").strip().lower() == "off":
        return False
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (MODE_SETTING,)).fetchone()
    except Exception:  # noqa: BLE001 - 還沒有 app_settings（初始化途中）：當沒設＝開
        return True
    return not (r and str(r["value"] or "").strip().lower() == "off")


def _allowed_tiers() -> tuple[str, ...]:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "correction_fields.json")
    try:
        with open(p, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return ("collect",)     # 設定讀不到：只認登入實測（最保守）
    for fd in cfg.get("fields", []):
        if fd.get("key") == "hostname":
            return tuple(t for t in TIERS if t in fd.get("correct_by", []))
    return ("collect",)


def _norm(h) -> str:
    import machine_identity

    return machine_identity.norm_name(h if isinstance(h, str) else (str(h) if h is not None else ""))


def _usable(n: str) -> bool:
    import machine_identity

    return bool(n) and not machine_identity.is_generic(n)


def same_name(a: str, b: str) -> bool:
    """兩個（已正規化：小寫、去網域）的主機名算不算同一個：完全相同才算。

    只差補 0（CIA SECSVR161-22 vs 機器 SECSVR161-022）**是不符**，要列給 CIA 改成機器的真實名字
    （使用者 2026-10-21 推翻 v1.482.2：「主機名稱以真實主機名稱為主」「這就是要抓的重點」）。
    大小寫／網域不同仍不算不符。命名規範（SECSVR<第三段>-<第四段>）不拿來校正任何東西。"""
    return a == b


def dns_name(pl: dict) -> str | None:
    """RVTools 這一列的 guest 主機名（vInfo「DNS Name」，VMware Tools 回報）。**VM 名不算**——
    VM 名是 vCenter 上的標籤，常被改名，不是機器自己講的名字。

    匯入時 hostname 欄是「DNS Name，空的才退回 VM 名」，所以：有 dns_name／raw 的直接用；
    舊匯入只有 hostname 時，hostname≠VM 名才能確定它來自 DNS Name；等於 VM 名＝分不出來，不採用。"""
    if "dns_name" in pl or isinstance(pl.get("raw"), dict):     # 新匯入：有明確的 DNS Name 欄（可能是空的）
        v = pl.get("dns_name") if "dns_name" in pl else pl["raw"].get("DNS Name")
        v = str(v).strip() if v is not None else ""
        return v or None
    h = str(pl.get("hostname") or "").strip()
    vm = str(pl.get("vm_name") or "").strip()
    if not h or _norm(h) == _norm(vm):
        return None
    return h


def _similarity(a: str, b: str) -> float:
    import difflib

    return difflib.SequenceMatcher(None, a, b).ratio()


def _fingerprint(conn) -> tuple:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}
    want = ["asset_serial", "hostname", "hostname_observed", "ip", "collect_ok", "asset_status"]
    expr = "||','||".join(f"quote({c})" if c in cols else "'NULL'" for c in want)
    n, blob = conn.execute(
        f"SELECT count(*), group_concat(x, char(10)) FROM (SELECT {expr} AS x FROM hardware)").fetchone()
    extra = []
    for sql in ("SELECT count(*), max(id) FROM source_record WHERE source = 'vcenter'",
                "SELECT count(*), max(scan_time) FROM scan_history"):
        try:
            extra.append(tuple(conn.execute(sql).fetchone()))
        except Exception:  # noqa: BLE001 - 舊庫沒有這張表：這一層證據不存在，指紋照算
            extra.append(None)
    d = hashlib.blake2b((blob or "").encode("utf-8", "surrogatepass"), digest_size=20).hexdigest()
    return (n, d, tuple(extra), _allowed_tiers())


def compute(conn) -> dict:
    """{fixes: {cia_serial: {...}}, ip_errors: [...]}。有快取（資料指紋不變就沿用）。"""
    try:
        key = _fingerprint(conn)
    except Exception:  # noqa: BLE001 - 還沒有 hardware 表：沒有可校正的
        return {"fixes": {}, "ip_errors": []}
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    out = _compute(conn)
    _CACHE.clear()
    _CACHE[key] = out
    return out


def _compute(conn) -> dict:
    import lifecycle

    tiers = _allowed_tiers()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}
    want = ["asset_serial", "hostname", "hostname_observed", "ip", "collect_ok", "collect_checked_at",
            "asset_status"]
    sel = ", ".join(c if c in cols else f"NULL AS {c}" for c in want)
    rows = [dict(r) for r in conn.execute(f"SELECT {sel} FROM hardware")
            if (r["asset_status"] or "").strip() not in lifecycle.RETIRED_STATUS]

    def ip_of(r):
        ip = str(r.get("ip") or "").strip()
        return ip if ip and ip != "0.0.0.0" else ""

    def off(s):
        return str(s or "").startswith(lifecycle.NON_CIA_PREFIXES)

    # 每個 IP 上，各層證據講的主機名（原字樣保留給畫面）
    said: dict[str, dict[str, dict[str, dict]]] = {}

    def add(ip, tier, name, at, via):
        n = _norm(name)
        if ip and _usable(n):
            import system_stats
            # 顯示／應改為一律用短名（使用者 2026-10-21：「我們預設不會加 domain」）
            said.setdefault(ip, {}).setdefault(tier, {}).setdefault(
                n, {"name": system_stats.strip_domain(str(name).strip()), "at": at, "via": via})

    for r in rows:
        ip = ip_of(r)
        if r.get("collect_ok") == 1:
            if off(r["asset_serial"]):
                add(ip, "collect", r.get("hostname"), r.get("collect_checked_at"), r["asset_serial"])
            elif str(r.get("hostname_observed") or "").strip():
                add(ip, "collect", r.get("hostname_observed"), r.get("collect_checked_at"), r["asset_serial"])
        elif off(r["asset_serial"]) and str(r["asset_serial"]).startswith("DYN-"):
            add(ip, "scan", r.get("hostname"), None, r["asset_serial"])
    rv_names: dict[str, set] = {}       # 主機名 → RVTools 看到它的 IP
    try:
        for r in conn.execute("SELECT payload, collected_at FROM source_record WHERE source = 'vcenter'"):
            try:
                pl = json.loads(r["payload"] or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(pl, dict):
                continue
            ip = str(pl.get("ip") or "").strip()
            if ip in ("", "0.0.0.0"):
                continue
            dns = dns_name(pl)
            if not dns:
                continue        # 沒有 DNS Name（只有 VM 名）不算主機名證據
            add(ip, "rvtools", dns, r["collected_at"], "RVTools")
            rv_names.setdefault(_norm(dns), set()).add(ip)
    except Exception:  # noqa: BLE001 - 舊庫沒有 source_record：少 RVTools 這層證據
        pass
    alive_ips: set[str] = set()
    try:
        for r in conn.execute(
                "SELECT DISTINCT ip FROM scan_history WHERE scan_ok = 1 "
                "AND scan_time >= datetime('now', 'localtime', ?)", (f"-{ALIVE_DAYS} days",)):
            if r["ip"]:
                alive_ips.add(str(r["ip"]).strip())
    except Exception:  # noqa: BLE001 - 舊庫沒有 scan_history：「近 N 天掃到」這條證據不存在
        pass

    # 名字 C 在哪些 IP「還活著」（收集過／近 N 天掃到／RVTools 有）
    alive_at: dict[str, set] = {}
    for r in rows:
        ip = ip_of(r)
        if not ip:
            continue
        for h in (r.get("hostname"), r.get("hostname_observed")):
            n = _norm(h)
            if _usable(n) and (r.get("collect_ok") == 1 or ip in alive_ips):
                alive_at.setdefault(n, set()).add(ip)
    for n, ips in rv_names.items():
        if _usable(n):
            alive_at.setdefault(n, set()).update(ips)

    cia_names_at: dict[str, set] = {}
    for r in rows:
        if not off(r["asset_serial"]) and ip_of(r):
            cia_names_at.setdefault(ip_of(r), set()).add(_norm(r.get("hostname")))

    fixes: dict[str, dict] = {}
    ip_errors: list[dict] = []
    for r in rows:
        s = r["asset_serial"]
        if off(s):
            continue
        ip, c = ip_of(r), _norm(r.get("hostname"))
        if not ip or not _usable(c):
            continue
        ev = said.get(ip, {})
        pick = None
        for t in TIERS:
            names = ev.get(t) or {}
            if len(names) > 1:
                break               # 同一層在同一個 IP 講出兩個名字：不知道誰對，不校正
            if len(names) == 1:
                n, e = next(iter(names.items()))
                pick = (t, n, e)
                break
        # 跟實際主機名一樣（大小寫／網域已正規化）就不校正；只差補 0 的照列（真實主機名為主，2026-10-21）
        if pick is None or same_name(pick[1], c):
            continue
        tier, h, e = pick
        if any(same_name(c, n) for n in (ev.get("collect") or {})):
            continue        # 登入實測講的就是 C：CIA 對，不看較弱的證據
        base = {"asset_serial": s, "cia_hostname": r.get("hostname"), "cia_ip": ip,
                "measured_hostname": e["name"], "tier": tier, "elsewhere_ip": None,
                "at": e["at"], "via": e["via"]}
        if len(cia_names_at.get(ip, set())) > 1:
            # 同一個 IP 有兩個以上不同名字的 CIA 登記（VIP／叢集別名／IP 回收）：不知道哪筆是打錯，全部不校正
            ip_errors.append({**base, "reason": "multi_cia"})
            continue
        if _similarity(c, h) < SIMILAR:
            # 名字差太多不像打錯字（例：secsvr018-091 vs bastion）：比較像 IP 回收給新機器，不校正
            ip_errors.append({**base, "reason": "not_similar"})
            continue
        if h in cia_names_at.get(ip, set()):
            # 同一個 IP 已經有一筆 CIA 登記就叫 H：C 比較像是舊機器（IP 回收）或另一筆，不是打錯字
            ip_errors.append({"asset_serial": s, "cia_hostname": r.get("hostname"), "cia_ip": ip,
                              "measured_hostname": e["name"], "tier": tier, "elsewhere_ip": None,
                              "at": e["at"], "via": e["via"], "cia_has_measured": True})
            continue
        elsewhere = sorted(alive_at.get(c, set()) - {ip})
        if elsewhere:
            ip_errors.append({"asset_serial": s, "cia_hostname": r.get("hostname"), "cia_ip": ip,
                              "measured_hostname": e["name"], "tier": tier,
                              "elsewhere_ip": elsewhere[0], "at": e["at"], "via": e["via"]})
            continue
        if tier not in tiers:
            ip_errors.append({"asset_serial": s, "cia_hostname": r.get("hostname"), "cia_ip": ip,
                              "measured_hostname": e["name"], "tier": tier, "elsewhere_ip": None,
                              "at": e["at"], "via": e["via"], "not_allowed": True})
            continue
        fixes[s] = {"measured": e["name"], "cia": r.get("hostname"), "ip": ip, "tier": tier,
                    "at": e["at"], "via": e["via"]}
    return {"fixes": fixes, "ip_errors": ip_errors}
