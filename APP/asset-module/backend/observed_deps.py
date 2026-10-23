"""觀察到的連線 → 系統上下游（2026-10-22 方案 B）。

## 為什麼要有

4-2 影響範圍查詢在一台核心 AIX 主機上只查得到 1 個受影響系統，因為系統對系統的資料
（system_deps）整張是空的——那張表要人工登記，沒有人登記過。真實的呼叫關係其實就在
每台機器的 TCP 連線上：服務盤點本來就跑 ss／netstat，同一份輸出裡的 ESTAB 列記下來
（host_connection），這裡把它推成「系統 T 呼叫系統 S」。

## 推法（方向約定同 system_deps／ci_edge：src 依賴 dst）

· 主機 H（系統 S）有一條 inbound 連線來自 P（系統 T）＝ T 呼叫 S ＝ **T 依賴 S**
· 主機 H（系統 T）有一條 outbound 連到 P（系統 S）　＝ T 呼叫 S ＝ **T 依賴 S**
· 「同一台」一律用 system_stats.machine_key（退役另組、不算），IP → 機器 → 該機器全部
  登記上的 APID；不另寫去重。IP 來源：hardware.ip（ips_of 拆多值）＋已收集的網卡 IP（asset_nic）。

## 不造邊、但一律計數（stats）——被排除的東西要講得出來，不可以安靜消失

· ignored     迴路／link-local／未指定位址
· collector   對方是收集器自己（收集時的 SSH 那一條連線）
· infra       管理埠（observed_deps_infra_ports；預設＝INFRA_PORTS 扣掉 22：RDP／WinRM／DNS／NTP／SNMP…）
· admin_ssh   :22 而且對方是跳板／PAM（observed_deps_admin_peers）。其餘 :22 照樣造邊、標「SSH/SFTP」
              ——系統間 SFTP／SCP 批次是真的依賴（2026-10-22 決定）
· excluded    app_settings observed_deps_exclude 列的 IP／CIDR／APID（監控、PAM 跳板、備份、LB SNAT）
· ambiguous   一個 IP 對到好幾台、而且它們的 APID 不一樣（共用 IP／VIP／舊登記）——不猜
· same_machine／same_system／host_no_system
· 對方對不到任何 APID → unknown_callers／unknown_callees（「未知來源」）
· 超過 observed_deps_stale_days（預設 30）天沒再看到的不讀

## 樞紐系統（hub）

監控、備份這種「跟所有人都有連線」的系統，照連線推會把整個機房串成一團：任何一台掛了
都經過它擴散到全部。fan-in 或 fan-out 超過門檻（有觀察邊的系統數 × observed_deps_hub_pct%，
預設 25%，且至少 observed_deps_hub_min，預設 10）就是樞紐：影響範圍照樣列它（直接受影響），
但經連線推論走到它之後**不再從它往外擴散**。回應裡會列出 hubs，畫面講得出為什麼停在那。

## 證據

每條邊帶它的 host_connection 列：evidence_count＝列數、observations＝這些列的 count 加總
（「觀察次數」：歷次收集看到的連線條數累加；兩端都收時同一條連線兩端各算一次，不去重）。
下鑽（edge_rows）跟邊的數字走同一份計算（infer），verify_numbers 釘住兩者一致。
"""
from __future__ import annotations

import ipaddress
import math
import re
from datetime import datetime, timedelta

STALE_KEY = "observed_deps_stale_days"
DEFAULT_STALE_DAYS = 30
EXCLUDE_KEY = "observed_deps_exclude"
HUB_PCT_KEY = "observed_deps_hub_pct"
DEFAULT_HUB_PCT = 25
HUB_MIN_KEY = "observed_deps_hub_min"
DEFAULT_HUB_MIN = 10
MAX_DEPTH_KEY = "observed_deps_max_depth"
#: 管理埠（不算業務呼叫、一律不造邊）。預設＝service_collector.INFRA_PORTS 扣掉 22：
#: SSH 22 在金融業常是系統間 SFTP／SCP 批次（MFT）——真的依賴，不可以一律丟（2026-10-22 決定）。
INFRA_PORTS_KEY = "observed_deps_infra_ports"
#: :22 只有對方是這些位址（跳板／PAM，IP 或 CIDR）時才當管理連線丟掉；收集器與排除清單本來就丟
ADMIN_PEERS_KEY = "observed_deps_admin_peers"
SSH_PORT = 22
#: 自動判定管理連線（預設開）：一個來源 IP 用 :22 連進「超過樞紐門檻」個不同系統 → 當跳板／PAM／自動化
#: （ansible 之類），它的 :22 列不造邊、計入 stats.admin_ssh、列在 auto_admin_ssh 讓人看得到、可推翻
#: （關掉這個開關，或把真的依賴從排除裡拿掉）。沒人填 observed_deps_admin_peers 時的保底。
AUTO_ADMIN_KEY = "observed_deps_auto_admin_ssh"
AUTO_ADMIN_LABEL = "自動判定為管理連線"
#: 證據上的協定標籤（畫面顯示用）
PORT_LABELS = {22: "SSH/SFTP"}
DEFAULT_MAX_DEPTH = 2
#: 一台主機在 host_connection 最多留幾組（超過刪最久沒看到的）
MAX_ROWS_PER_HOST = 2000
#: 邊上直接帶幾筆證據樣本（完整清單走 edge_rows）
SAMPLE_LIMIT = 10
CONFIDENCE = "推論"
EDGE_TYPE = "observed_conn"


def _int_setting(conn, key: str, default: int, minimum: int = 1) -> int:
    """設定值壞掉（非數字／太小）就用預設，不讓整張圖 500。"""
    import db

    try:
        v = int(db.get_setting(conn, key, str(default)) or default)
    except (TypeError, ValueError):
        return default
    return v if v >= minimum else default


def stale_days(conn) -> int:
    return _int_setting(conn, STALE_KEY, DEFAULT_STALE_DAYS)


def settings(conn) -> dict:
    import db

    return {
        "stale_days": stale_days(conn),
        "hub_pct": _int_setting(conn, HUB_PCT_KEY, DEFAULT_HUB_PCT),
        "hub_min": _int_setting(conn, HUB_MIN_KEY, DEFAULT_HUB_MIN),
        "max_depth": _int_setting(conn, MAX_DEPTH_KEY, DEFAULT_MAX_DEPTH),
        "exclude": db.get_setting(conn, EXCLUDE_KEY, "") or "",
        "infra_ports": _infra_ports(db.get_setting(conn, INFRA_PORTS_KEY, None)),
        "admin_peers": db.get_setting(conn, ADMIN_PEERS_KEY, "") or "",
        "auto_admin_ssh": (db.get_setting(conn, AUTO_ADMIN_KEY, "1") or "1").strip().lower()
        not in ("0", "off", "false", "no", "n"),
    }


def default_infra_ports() -> tuple:
    import service_collector

    return tuple(sorted(set(service_collector.INFRA_PORTS) - {SSH_PORT}))


def _infra_ports(raw: str | None) -> tuple:
    """observed_deps_infra_ports：逗號／空白分隔的埠號。沒設或一個合法的都沒有 → 預設。"""
    if raw is None or not str(raw).strip():
        return default_infra_ports()
    out = sorted({int(t) for t in re.split(r"[,;\s]+", str(raw)) if t.strip().isdigit()})
    return tuple(out) if out else default_infra_ports()


def port_label(port: int) -> str:
    import service_collector

    return PORT_LABELS.get(port) or service_collector.PORT_SERVICE_GUESS.get(port) or str(port)


def parse_exclude(raw: str) -> dict:
    """observed_deps_exclude：逗號／空白／換行分隔。像 IP 或 CIDR 的當網段，其餘當 APID。"""
    nets, apis = [], set()
    for tok in re.split(r"[,;\s]+", raw or ""):
        tok = tok.strip()
        if not tok:
            continue
        try:
            nets.append(ipaddress.ip_network(tok, strict=False))
        except ValueError:
            apis.add(tok)
    return {"nets": nets, "apis": apis}


def _in_nets(ip: str, nets) -> bool:
    if not nets or not ip:
        return False
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a.version == n.version and a in n for n in nets)


def collector_ips(conn) -> set[str]:
    """收集器自己的位址：納管用的那份設定（collector_ip→環境變數→自動偵測）＋備援位址
    （collector_ips_extra）＋這台機器的網卡位址。收集時的 SSH 連線就是從這些位址來的。"""
    import manage_state
    import onboard_engine

    out: set[str] = set()
    try:
        out.add(onboard_engine.resolve_collector_ip(conn))
    except Exception:  # noqa: BLE001 - 偵測不到就少一個位址；另外兩個來源照收，管理埠過濾仍會擋 SSH
        pass
    out.update(onboard_engine.resolve_extra_collector_ips(conn))
    out.update(_local_ips_cached(manage_state))
    return {ip for ip in out if ip}


_LOCAL_IPS: set[str] | None = None


def _local_ips_cached(manage_state) -> set[str]:
    global _LOCAL_IPS
    if _LOCAL_IPS is None:
        _LOCAL_IPS = set(manage_state.local_ips())
    return _LOCAL_IPS


# ── 寫入 ──────────────────────────────────────────────────────────────

def store(conn, host_ip: str, asset_serial: str | None, rows: list[dict],
          now: str | None = None) -> dict:
    """一台主機這次收到的連線（已由 service_collector.summarize_connections 彙總）寫進 host_connection。

    已有的同一組：count 累加、last_seen 更新；新的：first_seen＝last_seen＝now。
    這次沒看到的不動（靠門檻天數自然退場），超過 MAX_ROWS_PER_HOST 的刪最久沒看到的。
    **每台寫完就 commit**：整批收集可能跑很久，不可以一路抱著寫入鎖。
    回傳 {added, updated, pruned}。
    """
    from db import _now_local

    now = now or _now_local()
    added = updated = 0
    for r in rows or []:
        cur = conn.execute(
            "UPDATE host_connection SET count = count + ?, last_seen = ?, asset_serial = ? "
            "WHERE host_ip = ? AND direction = ? AND local_port = ? AND peer_ip = ? AND peer_port = ?",
            (int(r["count"]), now, asset_serial, host_ip, r["direction"], int(r["local_port"]),
             r["peer_ip"], int(r["peer_port"])))
        if cur.rowcount:
            updated += 1
            continue
        conn.execute(
            "INSERT INTO host_connection (host_ip, asset_serial, direction, local_port, peer_ip, "
            "peer_port, count, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
            (host_ip, asset_serial, r["direction"], int(r["local_port"]), r["peer_ip"],
             int(r["peer_port"]), int(r["count"]), now, now))
        added += 1
    pruned = 0
    total = conn.execute("SELECT COUNT(*) FROM host_connection WHERE host_ip = ?", (host_ip,)).fetchone()[0]
    if total > MAX_ROWS_PER_HOST:
        cur = conn.execute(
            "DELETE FROM host_connection WHERE id IN (SELECT id FROM host_connection WHERE host_ip = ? "
            "ORDER BY last_seen ASC, count ASC, id ASC LIMIT ?)", (host_ip, total - MAX_ROWS_PER_HOST))
        pruned = cur.rowcount or 0
    conn.commit()
    return {"added": added, "updated": updated, "pruned": pruned}


# ── IP → 機器 → 系統 ───────────────────────────────────────────────────

def _identity(conn) -> dict:
    """一次建好：serial→機器鍵、IP→機器鍵集合、機器鍵→APID 集合、APID→ci 節點 id。

    機器鍵＝lifecycle_prefix＋machine_key（全站唯一一把尺）。退役（ret:）不收進 IP 對照
    （IP 會回收給新機器），也不提供 APID。APID 用 TRIM 後的值當系統代碼（同 system_map），
    節點 id 用 ci_graph.bizsys_node_id(原值)——跟 ci_graph 建的節點是同一個。
    """
    import ci_graph
    import manage_state
    import system_stats

    key_of_serial: dict[str, str] = {}
    keys_of_ip: dict[str, set[str]] = {}
    apis_of_key: dict[str, set[str]] = {}
    nodes_of_api: dict[str, set[str]] = {}
    sig: list = []
    for r in conn.execute("SELECT asset_serial, hostname, ip, asset_status, api_id FROM hardware "
                          "ORDER BY asset_serial"):
        sig.append(tuple(r))
        k = system_stats.lifecycle_prefix(r) + system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        key_of_serial[r["asset_serial"]] = k
        if k.startswith("ret:"):
            continue
        api = (r["api_id"] or "").strip()
        if api:
            apis_of_key.setdefault(k, set()).add(api)
            nodes_of_api.setdefault(api, set()).add(ci_graph.bizsys_node_id(r["api_id"]))
        for ip in manage_state.ips_of(r["ip"]):
            keys_of_ip.setdefault(ip, set()).add(k)
    # 已收集的網卡 IP（次要 IP、VIP）：掛到那張卡所屬序號的那一台
    try:
        nic_rows = conn.execute("SELECT asset_serial, ip, ips FROM asset_nic ORDER BY id").fetchall()
    except Exception:  # noqa: BLE001 - 舊庫沒有 asset_nic：只用 hardware.ip，照常推
        nic_rows = []
    for r in nic_rows:
        sig.append(tuple(r))
        k = key_of_serial.get(r["asset_serial"])
        if not k or k.startswith("ret:"):
            continue
        for ip in manage_state.ips_of(",".join(x for x in (r["ip"], r["ips"]) if x)):
            keys_of_ip.setdefault(ip, set()).add(k)
    return {"key_of_serial": key_of_serial, "keys_of_ip": keys_of_ip, "apis_of_key": apis_of_key,
            "nodes_of_api": {a: sorted(v) for a, v in nodes_of_api.items()}, "sig": hash(tuple(sig))}


def _apis(ident: dict, keys: set[str]) -> set[str]:
    out: set[str] = set()
    for k in keys:
        out |= ident["apis_of_key"].get(k, set())
    return out


def _ambiguous(ident: dict, keys: set[str]) -> bool:
    """好幾台、而且各自的 APID 不一樣＝不知道是哪一台的連線。APID 一樣（同系統的 VIP）不算。"""
    if len(keys) < 2:
        return False
    return len({frozenset(ident["apis_of_key"].get(k, set())) for k in keys}) > 1


# ── 推論（帶快取）────────────────────────────────────────────────────────

#: {db 路徑: (簽章, 結果)}。簽章含 host_connection 統計、hardware／網卡內容雜湊、設定、門檻時點，
#: 任一變了就重算。同一個請求裡呼叫好幾次（system_map＋impact＋下鑽）只算一次；收集完會先算好。
_CACHE: dict[str, tuple] = {}


def _cutoff(days: int) -> str:
    # 取到整點：門檻時點在一小時內不變，快取才不會每秒失效
    t = (datetime.now() - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def _db_path(conn) -> str:
    try:
        return str(conn.execute("PRAGMA database_list").fetchone()[2] or ":memory:")
    except Exception:  # noqa: BLE001 - 拿不到路徑就不共用快取（當成一個獨立的鍵）
        return f"conn:{id(conn)}"


def infer(conn, use_cache: bool = True) -> dict:
    """全部 host_connection（門檻天數內）→ 系統對系統的邊。結果是共用的唯讀物件，呼叫端不可以改。

    回傳：
      edges: [{caller, callee, evidence_count, observations, first_seen, last_seen,
               hosts, ports, samples, row_ids}]       caller 依賴 callee（APID）
      hubs: [{api_id, fan_in, fan_out}]  hub_threshold
      nodes_of_api: {APID: [ci 節點 id]}
      unknown_callers[S] ／ unknown_callees[T]: {rows, peers}
      ambiguous: [{ip, rows, apis}]
      stats: {...}（見檔頭）  settings: {...}
    """
    st = settings(conn)
    ident = _identity(conn)
    cutoff = _cutoff(st["stale_days"])
    cips = collector_ips(conn)
    try:
        hc = tuple(conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(id),0), COALESCE(MAX(last_seen),''), COALESCE(SUM(count),0) "
            "FROM host_connection").fetchone())
        has_table = True
    except Exception:  # noqa: BLE001 - 舊庫還沒有 host_connection：等於沒有觀察資料（不是「沒有上下游」）
        hc, has_table = (0, 0, "", 0), False
    sig = (hc, ident["sig"], tuple(sorted(st.items())), cutoff, tuple(sorted(cips)), _identity_state())
    path = _db_path(conn)
    if use_cache and path in _CACHE and _CACHE[path][0] == sig:
        return _CACHE[path][1]
    result = _infer(conn, st, ident, cutoff, cips, has_table)
    if len(_CACHE) >= 8 and path not in _CACHE:   # 正式機只有一個庫；測試開很多暫存庫，別無限長
        _CACHE.clear()
    _CACHE[path] = (sig, result)
    return result


def invalidate() -> None:
    """清快取。machine_identity 換口徑／換待確認名單時會叫（同一台的判斷變了，邊就要重推）。"""
    _CACHE.clear()


def _identity_state() -> tuple:
    """同一台的判斷用到、但不在 hardware 列裡的狀態：口徑（machine_identity.mode）、
    待確認主機名名單、實測主機名校正。任何一個變了，快取就不能沿用。"""
    import machine_identity

    st = machine_identity._STATE
    return (machine_identity.mode(), hash(st.get("pending") or frozenset()),
            hash(tuple(sorted((st.get("host_fix") or {}).items()))))


def _infer(conn, st: dict, ident: dict, cutoff: str, cips: set[str], has_table: bool) -> dict:
    import service_collector

    excl = parse_exclude(st["exclude"])
    infra = set(st["infra_ports"])
    admin = parse_exclude(st["admin_peers"])["nets"]
    edges: dict[tuple[str, str], dict] = {}
    unknown_callers: dict[str, dict] = {}
    unknown_callees: dict[str, dict] = {}
    ambiguous: dict[str, dict] = {}
    pending: list[tuple] = []        # 先收齊，算完 :22 的扇出再決定哪些造邊
    # 被排除／模稜兩可／自動判定管理連線的列 id：畫面上的筆數點下去要列得出是哪幾列（bucket_rows）
    bucket_ids: dict[str, list] = {"excluded": [], "ambiguous": [], "admin_ssh_auto": []}
    stats = {"rows": 0, "used": 0, "ignored": 0, "collector": 0, "infra": 0, "admin_ssh": 0,
             "admin_ssh_auto": 0, "excluded": 0,
             "ambiguous": 0, "same_machine": 0, "same_system": 0, "host_no_system": 0, "unknown": 0}
    rows = conn.execute(
        "SELECT id, host_ip, asset_serial, direction, local_port, peer_ip, peer_port, count, "
        "first_seen, last_seen FROM host_connection WHERE last_seen >= ? ORDER BY id",
        (cutoff,)).fetchall() if has_table else []
    key_of_serial, keys_of_ip = ident["key_of_serial"], ident["keys_of_ip"]
    # 同一個 IP／同一台在幾十萬列裡會重複出現：解析結果記起來，每個只算一次
    ip_memo: dict[str, tuple] = {}
    host_memo: dict[tuple, tuple] = {}

    def ip_info(ip: str) -> tuple:
        """(可忽略, 在排除網段, 機器鍵, 模稜兩可, APID)"""
        v = ip_memo.get(ip)
        if v is None:
            keys = frozenset(keys_of_ip.get(ip, ()))
            v = ip_memo[ip] = (service_collector.is_ignorable_peer(ip), _in_nets(ip, excl["nets"]),
                               keys, _ambiguous(ident, set(keys)), frozenset(_apis(ident, set(keys))))
        return v

    def host_info(serial: str | None, ip: str) -> tuple:
        """收集的那台：序號優先（收集時的代表序號），序號不在了（被併回）就用 IP。(機器鍵, 模稜兩可, APID)"""
        v = host_memo.get((serial, ip))
        if v is None:
            k = key_of_serial.get(serial or "")
            if k and not k.startswith("ret:"):
                keys = frozenset({k})
                v = (keys, False, frozenset(_apis(ident, {k})))
            else:
                _ig, _ex, keys, amb, apis = ip_info(ip)
                v = (keys, amb, apis)
            host_memo[(serial, ip)] = v
        return v

    for r in rows:
        stats["rows"] += 1
        peer, host_ip, inbound = r["peer_ip"], r["host_ip"], r["direction"] == "inbound"
        p_ign, p_excl, peer_keys, p_amb, peer_apis = ip_info(peer)
        if p_ign:
            stats["ignored"] += 1
            continue
        if peer in cips:
            stats["collector"] += 1
            continue
        svc_port = r["local_port"] if inbound else r["peer_port"]
        if svc_port in infra:
            stats["infra"] += 1
            continue
        if svc_port == SSH_PORT and admin and (_in_nets(peer, admin) or _in_nets(host_ip, admin)):
            stats["admin_ssh"] += 1          # 跳板／PAM 的 SSH：管理連線，不是系統呼叫
            continue
        if p_excl or ip_info(host_ip)[1]:
            stats["excluded"] += 1
            bucket_ids["excluded"].append(r["id"])
            continue
        host_keys, h_amb, host_apis = host_info(r["asset_serial"], host_ip)
        amb_ip = host_ip if h_amb else peer if p_amb else None
        if amb_ip:
            stats["ambiguous"] += 1
            bucket_ids["ambiguous"].append(r["id"])
            a = ambiguous.setdefault(amb_ip, {"ip": amb_ip, "rows": 0, "apis": set()})
            a["rows"] += 1
            a["apis"] |= ip_info(amb_ip)[4]
            continue
        if host_keys & peer_keys:
            stats["same_machine"] += 1
            continue
        if not host_apis:
            stats["host_no_system"] += 1
            continue
        if (host_apis | peer_apis) & excl["apis"]:
            host_apis, peer_apis = host_apis - excl["apis"], peer_apis - excl["apis"]
            if not host_apis or not peer_apis:
                stats["excluded"] += 1
                bucket_ids["excluded"].append(r["id"])
                continue
        if not peer_apis:
            stats["unknown"] += 1
            bucket = unknown_callers if inbound else unknown_callees
            for a in sorted(host_apis):
                u = bucket.setdefault(a, {"rows": 0, "peers": set()})
                u["rows"] += 1
                u["peers"].add(peer)
            continue
        callers, callees = (peer_apis, host_apis) if inbound else (host_apis, peer_apis)
        port = r["local_port"] if inbound else r["peer_port"]
        pending.append((r, callers, callees, int(port), host_ip if inbound else peer,
                        peer if inbound else host_ip))

    # 門檻跟樞紐同一把尺：有觀察邊（候選）的系統數 × hub_pct%，且至少 hub_min
    cand_sys = {x for (_r, cs, ce, *_rest) in pending for t in cs for x in ce if t != x} | \
        {t for (_r, cs, ce, *_rest) in pending for t in cs for x in ce if t != x}
    threshold = max(st["hub_min"], math.ceil(len(cand_sys) * st["hub_pct"] / 100))

    # 自動判定管理連線：同一個來源 IP 用 :22 連進超過門檻個不同系統
    auto_admin: dict[str, dict] = {}
    if st["auto_admin_ssh"]:
        ssh_targets: dict[str, set] = {}
        for (_r, cs, ce, port, _cip, src_ip) in pending:
            if port == SSH_PORT:
                ssh_targets.setdefault(src_ip, set()).update(x for x in ce if x not in cs)
        for src_ip, tg in ssh_targets.items():
            if len(tg) > threshold:
                auto_admin[src_ip] = {"ip": src_ip, "apis": set(), "targets": len(tg), "rows": 0,
                                      "label": AUTO_ADMIN_LABEL}

    for (r, callers, callees, port, callee_ip, src_ip) in pending:
        if port == SSH_PORT and src_ip in auto_admin:
            stats["admin_ssh"] += 1
            stats["admin_ssh_auto"] += 1
            bucket_ids["admin_ssh_auto"].append(r["id"])
            auto_admin[src_ip]["rows"] += 1
            auto_admin[src_ip]["apis"] |= set(callers)
            continue
        used = False
        for t in sorted(callers):
            for s in sorted(callees):
                if t == s:
                    stats["same_system"] += 1
                    continue
                used = True
                e = edges.get((t, s))
                if e is None:
                    e = edges[(t, s)] = {
                        "caller": t, "callee": s, "evidence_count": 0, "observations": 0,
                        "first_seen": None, "last_seen": None, "hosts": set(), "ports": set(),
                        "row_ids": [], "samples": []}
                e["evidence_count"] += 1
                e["observations"] += int(r["count"] or 0)
                e["row_ids"].append(r["id"])
                e["hosts"].add(callee_ip)
                e["ports"].add(port)
                if r["first_seen"] and (e["first_seen"] is None or r["first_seen"] < e["first_seen"]):
                    e["first_seen"] = r["first_seen"]
                if r["last_seen"] and (e["last_seen"] is None or r["last_seen"] > e["last_seen"]):
                    e["last_seen"] = r["last_seen"]
                if len(e["samples"]) < SAMPLE_LIMIT:
                    e["samples"].append(_row_view(r))
        if used:
            stats["used"] += 1
    out_edges = []
    for e in sorted(edges.values(), key=lambda x: (x["caller"], x["callee"])):
        e["hosts"] = sorted(e["hosts"])
        e["ports"] = sorted(e["ports"])
        e["protocols"] = sorted({port_label(x) for x in e["ports"]})
        out_edges.append(e)

    # 樞紐：fan-in／fan-out 超過門檻
    fan_in: dict[str, set] = {}
    fan_out: dict[str, set] = {}
    for e in out_edges:
        fan_in.setdefault(e["callee"], set()).add(e["caller"])
        fan_out.setdefault(e["caller"], set()).add(e["callee"])
    hubs = [{"api_id": a, "fan_in": len(fan_in.get(a, ())), "fan_out": len(fan_out.get(a, ()))}
            for a in sorted(set(fan_in) | set(fan_out))
            if len(fan_in.get(a, ())) > threshold or len(fan_out.get(a, ())) > threshold]

    def _fin(b: dict) -> dict:
        return {k: {"rows": v["rows"], "peers": sorted(v["peers"])} for k, v in sorted(b.items())}

    return {"edges": out_edges, "hubs": hubs, "hub_threshold": threshold,
            "auto_admin_ssh": [{**v, "apis": sorted(v["apis"])}
                               for v in sorted(auto_admin.values(), key=lambda x: x["ip"])],
            "nodes_of_api": ident["nodes_of_api"],
            "unknown_callers": _fin(unknown_callers), "unknown_callees": _fin(unknown_callees),
            "ambiguous": [{"ip": a["ip"], "rows": a["rows"], "apis": sorted(a["apis"])}
                          for a in sorted(ambiguous.values(), key=lambda x: x["ip"])],
            "stats": stats, "bucket_ids": bucket_ids,
            "settings": {k: (list(v) if isinstance(v, tuple) else v) for k, v in st.items()
                         if k not in ("exclude", "admin_peers")}}


def _row_view(r) -> dict:
    return {"id": r["id"], "host_ip": r["host_ip"], "asset_serial": r["asset_serial"],
            "direction": r["direction"], "local_port": r["local_port"], "peer_ip": r["peer_ip"],
            "peer_port": r["peer_port"], "count": r["count"], "first_seen": r["first_seen"],
            "last_seen": r["last_seen"]}


def edge_rows(conn, caller: str, callee: str, inferred: dict | None = None) -> list[dict]:
    """一條邊的完整證據（下鑽）：跟 infer 的 evidence_count 同一份計算。"""
    inferred = inferred or infer(conn)
    e = next((x for x in inferred["edges"] if x["caller"] == caller and x["callee"] == callee), None)
    if not e or not e["row_ids"]:
        return []
    out: list[dict] = []
    ids = e["row_ids"]
    for i in range(0, len(ids), 500):          # SQLite 參數上限
        chunk = ids[i:i + 500]
        ph = ",".join("?" for _ in chunk)
        out += [_row_view(r) for r in conn.execute(
            "SELECT id, host_ip, asset_serial, direction, local_port, peer_ip, peer_port, count, "
            f"first_seen, last_seen FROM host_connection WHERE id IN ({ph}) ORDER BY id", chunk)]
    return out


BUCKETS = ("excluded", "ambiguous", "admin_ssh_auto")


def bucket_rows(conn, bucket: str, inferred: dict | None = None, limit: int = 5000) -> dict:
    """畫面上「排除 N／模稜兩可 N／自動判定管理連線 N」點下去的那幾列（跟 stats 同一份計算）。"""
    if bucket not in BUCKETS:
        raise ValueError(f"不合法的分類：{bucket}；合法值：{'、'.join(BUCKETS)}")
    inferred = inferred or infer(conn)
    ids = inferred.get("bucket_ids", {}).get(bucket, [])
    out: list[dict] = []
    for i in range(0, min(len(ids), limit), 500):
        chunk = ids[i:i + 500]
        ph = ",".join("?" for _ in chunk)
        out += [_row_view(r) for r in conn.execute(
            "SELECT id, host_ip, asset_serial, direction, local_port, peer_ip, peer_port, count, "
            f"first_seen, last_seen FROM host_connection WHERE id IN ({ph}) ORDER BY id", chunk)]
    return {"bucket": bucket, "total": len(ids), "rows": out, "truncated": len(ids) > limit}


def hub_ids(inferred: dict | None) -> set[str]:
    return {h["api_id"] for h in (inferred or {}).get("hubs", [])}


def evidence_view(e: dict) -> dict:
    """給 API 的證據摘要（不含內部 row_ids）。observations＝觀察次數。"""
    return {"count": e["evidence_count"], "observations": e["observations"],
            "first_seen": e["first_seen"], "last_seen": e["last_seen"],
            "hosts": e["hosts"], "ports": e["ports"], "protocols": e["protocols"],
            "samples": e["samples"]}
