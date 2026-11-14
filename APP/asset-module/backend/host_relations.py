"""主機關聯探索：以一台主機為起點，讀已存的連線觀測，一次回一跳的鄰居（工作包 B，只讀）。

規格：docs/host-relations/規格_主機關聯探索_v1.0_2026-11-06.md
對照：docs/host-relations/A_對照與契約_2026-11-08.md

## 只讀
只讀 host_connection、hardware、asset_nic、manage_state 的結果，不連線、不收集、不寫庫。
展開一個節點不會登入任何主機；沒有觀測就回「沒有證據」，不去探測。

## 方向（跟 observed_deps 的 provider → dependent 不同）
邊＝「推定通訊來源 → 推定通訊目的」：
  outbound：收集端 → peer，目的埠＝peer_port（local_port＝0 是哨兵）
  inbound ：peer → 收集端，目的埠＝local_port（peer_port＝0 是哨兵）
方向是收集器依本機 LISTEN 快照推的 → direction_status＝inferred。目的埠＝0（缺資料）→ uncertain、
不給方向、不顯示 TCP/0。confirmed 保留給未來有可驗證證據的來源，這裡不用。

## 身分
同一台一律用 observed_deps._identity（machine_key，全站唯一一把尺）。IP 唯一對到一台現役機器 → host 節點；
對不到或對到多台 → endpoint 節點（不猜是哪一台）。

## 過濾
沿用 observed_deps 的規則與名稱（ignored／collector／infra／admin_ssh／admin_ssh_auto／excluded／same_machine），
被排除的列一律計數（excluded_summary），不安靜消失：shown_rows＋各排除數＝scope_rows。

## 一致讀取（snapshot）
後端是單一 uvicorn 行程，snapshot 放行程內記憶體：第一次查某個節點時算好整份結果凍住，分頁從凍住的結果切。
同一個 snapshot 再查別的節點時，若資料版本已變 → SnapshotStale（409），請使用者重新讀取，不混兩個版本。
cursor 有 HMAC，竄改回 400；snapshot 逾時回 410。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta

FLAG_KEY = "host_relations"
FLAG_LABEL = "主機關聯探索（未上線驗證）"
STALE_WARN_KEY = "host_relations_stale_warn_days"
DEFAULT_STALE_WARN_DAYS = 7
#: 2026-11-02 01:42 以前 db._now_local 用主機自己的時區（沒查證），之後明確寫台北時間（v2.041.58）。
#: 之前的時間預設不換算（time_quality＝unknown）；查證後可把這個設定設成 Asia/Taipei。
LEGACY_TZ_KEY = "host_relations_legacy_tz"
TAIPEI_SINCE = "2026-11-02 01:42:16"
SCHEMA_VERSION = "1"
NAMESPACE = "default"
DEFAULT_LIMIT = 50
MAX_LIMIT = 50
DIRECTIONS = ("outbound", "inbound", "both")
PROTOCOLS = ("tcp",)
EVIDENCE_KINDS = ("observed_socket",)
MAX_PORT_FILTERS = 20
MAX_NODE_ID_LEN = 300
TS_FMT = "%Y-%m-%d %H:%M:%S"
EXCLUDE_REASONS = ("ignored", "collector", "infra", "admin_ssh", "admin_ssh_auto", "excluded", "same_machine")

SNAPSHOT_TTL_SEC = 600
MAX_SNAPSHOTS = 128
MAX_RESULTS_PER_SNAPSHOT = 64
MAX_REFS_PER_EDGE = 20


class RelationError(Exception):
    """帶 HTTP 狀態與穩定錯誤代碼。路由層原樣轉成 HTTPException。"""
    status = 400
    code = "bad_request"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotEnabled(RelationError):
    status, code = 404, "not_enabled"


class NodeNotFound(RelationError):
    status, code = 404, "node_not_found"


class BadParam(RelationError):
    status, code = 422, "invalid_param"


class BadCursor(RelationError):
    status, code = 400, "invalid_cursor"


class SnapshotStale(RelationError):
    status, code = 409, "snapshot_stale"


class SnapshotExpired(RelationError):
    status, code = 410, "snapshot_expired"


# ── 設定 ──────────────────────────────────────────────────────────────

def enabled(conn) -> bool:
    """旗標沒登記＝關（前端 isEnabled 找不到會當開，所以後端自己擋）。"""
    try:
        row = conn.execute("SELECT enabled FROM feature_flags WHERE module_key = ?", (FLAG_KEY,)).fetchone()
    except Exception:  # noqa: BLE001 - 舊庫沒有 feature_flags：當成沒開（fail-closed）
        return False
    return bool(row) and bool(row[0])


def stale_warn_days(conn) -> int:
    import db

    try:
        v = int(db.get_setting(conn, STALE_WARN_KEY, str(DEFAULT_STALE_WARN_DAYS)) or DEFAULT_STALE_WARN_DAYS)
    except (TypeError, ValueError):
        return DEFAULT_STALE_WARN_DAYS
    return v if v >= 1 else DEFAULT_STALE_WARN_DAYS


def _legacy_tz(conn) -> str | None:
    import db

    v = (db.get_setting(conn, LEGACY_TZ_KEY, "") or "").strip()
    return "Asia/Taipei" if v in ("Asia/Taipei", "+08:00", "UTC+08:00") else None


def ts_view(raw: str | None, legacy_tz: str | None = None) -> dict | None:
    """無時區字串 → {raw, utc, time_quality}。只有確定是台北時間的才換算成 UTC，其餘 utc＝None。"""
    if not raw:
        return None
    try:
        dt = datetime.strptime(raw, TS_FMT)
    except (TypeError, ValueError):
        return {"raw": raw, "utc": None, "time_quality": "unknown"}
    if raw >= TAIPEI_SINCE:
        quality = "taipei"
    elif legacy_tz:
        quality = "taipei_configured"
    else:
        return {"raw": raw, "utc": None, "time_quality": "unknown"}
    return {"raw": raw, "utc": (dt - timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ"), "time_quality": quality}


# ── 節點 ID ───────────────────────────────────────────────────────────

def host_node_id(machine_key: str) -> str:
    return "h:" + machine_key


def endpoint_node_id(ip: str) -> str:
    a = ipaddress.ip_address(ip)
    return f"e:{NAMESPACE}:{a.version}:{a.compressed}"


def parse_node_id(node_id: str) -> tuple[str, str]:
    """→ ("host", machine_key) 或 ("endpoint", ip)。格式不對丟 BadParam（不回原字串內容，避免反射）。"""
    if not isinstance(node_id, str) or not node_id or len(node_id) > MAX_NODE_ID_LEN:
        raise BadParam("node_id 格式不正確")
    if node_id.startswith("h:") and len(node_id) > 2:
        return "host", node_id[2:]
    if node_id.startswith("e:"):
        parts = node_id.split(":", 3)
        if len(parts) == 4 and parts[1] == NAMESPACE and parts[2] in ("4", "6"):
            try:
                a = ipaddress.ip_address(parts[3])
            except ValueError as exc:
                raise BadParam("node_id 格式不正確") from exc
            if str(a.version) == parts[2] and a.compressed == parts[3]:
                return "endpoint", a.compressed
    raise BadParam("node_id 格式不正確")


# ── 資料版本與身分 ────────────────────────────────────────────────────

def data_signature(conn) -> tuple:
    """資料版本：host_connection 統計＋身分簽章＋過濾設定。任一變了就是新版本。"""
    import observed_deps

    try:
        hc = tuple(conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(id),0), COALESCE(MAX(last_seen),''), COALESCE(SUM(count),0) "
            "FROM host_connection").fetchone())
    except Exception:  # noqa: BLE001 - 舊庫沒有 host_connection：版本＝空資料（查詢也會回沒有觀測）
        hc = (0, 0, "", 0)
    try:
        hs = tuple(conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(id),0), COALESCE(MAX(last_seen),'') FROM host_service").fetchone())
    except Exception:  # noqa: BLE001 - 舊庫沒有 host_service：服務名稱只用常見埠號對照
        hs = (0, 0, "")
    ident = observed_deps._identity(conn)
    st = observed_deps.settings(conn)
    # 跟 observed_deps 的快取簽章同一組：同一台的口徑（_identity_state）、收集器位址變了也算新版本
    return (hc, hs, ident["sig"], observed_deps._identity_state(), tuple(sorted(observed_deps.collector_ips(conn))),
            tuple(sorted((k, str(v)) for k, v in st.items())), stale_warn_days(conn), _legacy_tz(conn))


def _management(conn, key_of_serial: dict) -> tuple[dict, str | None]:
    """machine_key → (status, reason_code, detail)。拿不到（例外）→ ({}, 警告代碼)，節點標 unknown。

    已納管／已收集 → managed；退役不會出現（身分解析不收退役）；非納管設備 → unmanaged（exempt）；
    其他要處理的狀態 → unmanaged，detail 帶原本的中文狀態。access_failed 只是「連不上」，不推導 down。
    """
    import manage_state as ms

    try:
        items = ms.summarize(conn)["items"]
    except Exception:  # noqa: BLE001 - 納管狀態算不出來：回警告，節點顯示「納管狀態未知」，不偽裝成未納管
        return {}, "management_unavailable"
    by_key: dict[str, list] = {}
    for it in items:
        k = key_of_serial.get(it.get("asset_serial") or "")
        if k and not k.startswith("ret:"):
            by_key.setdefault(k, []).append(it)
    reason_of = {ms.NOT_ONBOARDED: "not_onboarded", ms.UNREGISTERED: "not_in_inventory",
                 ms.LOST: "not_seen_in_scan", ms.ONBOARDED_STALE: "access_failed",
                 ms.NOT_COVERED: "not_covered", ms.CONFLICT: "needs_review", ms.NO_IP: "no_ip",
                 ms.EXEMPT: "exempt", ms.RETIRED: "confirmed_retired", ms.RETIRED_ALIVE: "needs_review"}
    out = {}
    for k, g in by_key.items():
        mstates = {x.get("machine_state") for x in g if x.get("machine_state")}
        if len(mstates) == 1:
            state = next(iter(mstates))
        else:   # 代表筆口徑：同 manage_state._aggregate（要處理的優先，再取最小序號）
            state = min(g, key=lambda x: (0 if x["state"] in ms.NEEDS_ACTION_STATES else 1,
                                          x.get("asset_serial") or ""))["state"]
        if state in (ms.ONBOARDED, ms.COLLECTED):
            out[k] = ("managed", "managed", state)
        else:
            out[k] = ("unmanaged", reason_of.get(state, "unknown"), state)
    return out, None


class _Ctx:
    """一次查詢用到的身分、過濾設定、涵蓋統計。"""

    def __init__(self, conn):
        import observed_deps as od

        self.ident = od._identity(conn)
        st = od.settings(conn)
        self.excl = od.parse_exclude(st["exclude"])
        self.infra = set(st["infra_ports"])
        self.admin = od.parse_exclude(st["admin_peers"])["nets"]
        self.cips = od.collector_ips(conn)
        self.auto_admin_ips = ({a["ip"] for a in od.infer(conn)["auto_admin_ssh"]}
                               if st["auto_admin_ssh"] else set())
        self.legacy_tz = _legacy_tz(conn)
        self.warn_days = stale_warn_days(conn)
        self.warn_cutoff = (datetime.now() - timedelta(days=self.warn_days)).strftime(TS_FMT)
        self.max_rows = od.MAX_ROWS_PER_HOST
        self.ips_of_key: dict[str, set] = {}
        for ip, keys in self.ident["keys_of_ip"].items():
            for k in keys:
                self.ips_of_key.setdefault(k, set()).add(ip)
        self.serials_of_key: dict[str, list] = {}
        for s, k in self.ident["key_of_serial"].items():
            if not k.startswith("ret:"):
                self.serials_of_key.setdefault(k, []).append(s)
        self.hostname: dict[str, str] = {}
        for r in conn.execute("SELECT asset_serial, hostname FROM hardware ORDER BY asset_serial"):
            k = self.ident["key_of_serial"].get(r["asset_serial"])
            if k and not k.startswith("ret:") and k not in self.hostname and (r["hostname"] or "").strip():
                self.hostname[k] = r["hostname"].strip()
        self.mgmt, self.mgmt_warning = _management(conn, self.ident["key_of_serial"])
        # 涵蓋：每個收集 IP 的列數（2,000 上限是每個 host_ip）、每台的最後收集時間
        self.rows_of_ip: dict[str, int] = {}
        self.last_of_key: dict[str, str] = {}
        self.rows_of_key: dict[str, int] = {}
        try:
            cov = conn.execute("SELECT host_ip, asset_serial, COUNT(*) AS n, MAX(last_seen) AS last "
                               "FROM host_connection GROUP BY host_ip, asset_serial").fetchall()
        except Exception:  # noqa: BLE001 - 舊庫沒有 host_connection：每台都是「沒有收集」，查詢回 partial
            cov = []
        for r in cov:
            self.rows_of_ip[r["host_ip"]] = self.rows_of_ip.get(r["host_ip"], 0) + r["n"]
            k = self.collector_node_key(r["asset_serial"], r["host_ip"])
            if k:
                self.rows_of_key[k] = self.rows_of_key.get(k, 0) + r["n"]
                if r["last"] and r["last"] > self.last_of_key.get(k, ""):
                    self.last_of_key[k] = r["last"]
        self._ip_memo: dict[str, tuple] = {}
        # 服務名稱：目的那台的服務盤點（host_service）有記行程名就用（機器講的）；沒有才用常見埠號對照
        self.svc: dict[tuple, tuple] = {}
        try:
            srows = conn.execute("SELECT ip, asset_serial, port, process, last_seen FROM host_service "
                                 "WHERE proto = 'tcp' AND process IS NOT NULL AND TRIM(process) <> '' "
                                 "ORDER BY last_seen").fetchall()
        except Exception:  # noqa: BLE001 - 舊庫沒有 host_service：只用常見埠號對照（basis 會標 common_port）
            srows = []
        for r in srows:
            k = self.ident["key_of_serial"].get(r["asset_serial"] or "")
            if not k or k.startswith("ret:"):
                ks = self.ident["keys_of_ip"].get(r["ip"], set())
                k = next(iter(ks)) if len(ks) == 1 else None
            owner = host_node_id(k) if k else None
            if owner:
                self.svc[(owner, int(r["port"]))] = (r["process"].strip(), r["last_seen"])

    def service_of(self, node_id: str, port: int | None) -> dict:
        """目的端點＋目的埠 → 服務名稱與依據。行程名（listening_process）比埠號猜的（common_port）可信。"""
        import observed_deps as od
        import service_collector

        if port is None:
            return {"name": None, "basis": None}
        hit = self.svc.get((node_id, port))
        if hit:
            return {"name": hit[0], "basis": "listening_process", "observed_at": ts_view(hit[1], self.legacy_tz)}
        guess = od.PORT_LABELS.get(port) or service_collector.PORT_SERVICE_GUESS.get(port)
        return {"name": guess, "basis": "common_port" if guess else None}

    def live_key(self, mkey: str) -> bool:
        return mkey in self.serials_of_key

    def resolve_ip(self, ip: str) -> tuple[str, int]:
        """IP → (節點 id, 候選台數)。唯一一台 → host；0 或多台 → endpoint。"""
        v = self._ip_memo.get(ip)
        if v is None:
            keys = self.ident["keys_of_ip"].get(ip, set())
            if len(keys) == 1:
                v = (host_node_id(next(iter(keys))), 1)
            else:
                v = (endpoint_node_id(ip), len(keys))
            self._ip_memo[ip] = v
        return v

    def is_retired_serial(self, serial: str | None) -> bool:
        """收集時的代表序號現在是退役機器：這列是舊機器的觀測，不可以靠 IP 算到接手同 IP 的新機器頭上。"""
        return bool(serial) and (self.ident["key_of_serial"].get(serial) or "").startswith("ret:")

    def collector_node_key(self, serial: str | None, host_ip: str) -> str | None:
        k = self.ident["key_of_serial"].get(serial or "")
        if k:
            return None if k.startswith("ret:") else k
        keys = self.ident["keys_of_ip"].get(host_ip, set())
        return next(iter(keys)) if len(keys) == 1 else None

    def collector_node(self, serial: str | None, host_ip: str) -> str:
        k = self.collector_node_key(serial, host_ip)
        return host_node_id(k) if k else self.resolve_ip(host_ip)[0]

    def apis_of_node(self, node_id: str) -> set:
        if node_id.startswith("h:"):
            return set(self.ident["apis_of_key"].get(node_id[2:], set()))
        return set()

    def ips_of_node(self, node_id: str) -> set:
        kind, val = parse_node_id(node_id)
        return set(self.ips_of_key.get(val, set())) if kind == "host" else {val}


# ── 核心：列 → 邊 ────────────────────────────────────────────────────

def _fetch_rows(conn, ctx: _Ctx, kind: str, val: str) -> list:
    """碰到這個節點的全部列。三段各走自己的索引（host_ip 在 UNIQUE 索引最前面、peer_ip、asset_serial），
    UNION 去掉重複的列——寫成一個 OR 的話 SQLite 會整張表掃。"""
    if kind == "host":
        ips = sorted(ctx.ips_of_key.get(val, set()))
        serials = sorted(ctx.serials_of_key.get(val, []))
    else:
        ips, serials = [val], []
    if not ips and not serials:
        return []
    cols = "id, host_ip, asset_serial, direction, local_port, peer_ip, peer_port, count, first_seen, last_seen"
    parts, params = [], []
    if ips:
        ph = ",".join("?" * len(ips))
        parts += [f"SELECT {cols} FROM host_connection WHERE host_ip IN ({ph})",
                  f"SELECT {cols} FROM host_connection WHERE peer_ip IN ({ph})"]
        params += ips + ips
    if serials:
        parts.append(f"SELECT {cols} FROM host_connection WHERE asset_serial IN ({','.join('?' * len(serials))})")
        params += serials
    try:
        return conn.execute(" UNION ".join(parts) + " ORDER BY id", params).fetchall()
    except Exception:  # noqa: BLE001 - 舊庫沒有 host_connection：沒有觀測（completeness 會標 root_not_collected）
        return []


def fetched_count(conn, ctx: _Ctx, root: str) -> int:
    """碰到這個節點的原始列數（verify_numbers 拿來跟 excluded_summary.fetched_rows 對）。"""
    kind, val = parse_node_id(root)
    return len(_fetch_rows(conn, ctx, kind, val))


def _exclude_reason(ctx: _Ctx, r, src_ip: str | None, port: int | None, host_node: str, peer_node: str) -> str | None:
    """跟 observed_deps._infer 同一套規則、同一個順序（同一台在 :22 自動判定之前，_infer 也是先擋同一台）。"""
    import observed_deps as od
    import service_collector

    peer, host_ip = r["peer_ip"], r["host_ip"]
    if service_collector.is_ignorable_peer(peer):
        return "ignored"
    if peer in ctx.cips:
        return "collector"
    if port is not None and port in ctx.infra:
        return "infra"
    if port == od.SSH_PORT and ctx.admin and (od._in_nets(peer, ctx.admin) or od._in_nets(host_ip, ctx.admin)):
        return "admin_ssh"
    if od._in_nets(peer, ctx.excl["nets"]) or od._in_nets(host_ip, ctx.excl["nets"]):
        return "excluded"
    if host_node == peer_node:
        return "same_machine"
    if ctx.excl["apis"]:
        for n in (host_node, peer_node):
            apis = ctx.apis_of_node(n)
            if apis and apis <= ctx.excl["apis"]:
                return "excluded"
    if port == od.SSH_PORT and src_ip in ctx.auto_admin_ips:
        return "admin_ssh_auto"
    return None


def compute(conn, ctx: _Ctx, root: str, direction: str, filters: dict) -> dict:
    """一個節點、一個方向的完整結果（全部邊，已排序）。分頁在外面切。

    每一列都有去處（excluded_summary）：
      fetched_rows ＝ scope_rows ＋ retired_collector ＋ other_node ＋ other_direction ＋ port_filtered
      scope_rows   ＝ shown_rows ＋ 各排除原因
    """
    kind, val = parse_node_id(root)
    rows = _fetch_rows(conn, ctx, kind, val)
    edges: dict[str, dict] = {}
    summary = {"fetched_rows": len(rows), "retired_collector": 0, "other_node": 0, "other_direction": 0,
               "port_filtered": 0, "scope_rows": 0, "shown_rows": 0, **{k: 0 for k in EXCLUDE_REASONS}}
    ports_f = set(filters.get("port") or ())
    for r in rows:
        if ctx.is_retired_serial(r["asset_serial"]):
            summary["retired_collector"] += 1      # 退役機器收的列：不算到接手同 IP 的新機器
            continue
        host_node = ctx.collector_node(r["asset_serial"], r["host_ip"])
        peer_node, _n = ctx.resolve_ip(r["peer_ip"])
        inbound = r["direction"] == "inbound"
        raw_port = int(r["local_port"] if inbound else r["peer_port"])
        known = r["direction"] in ("inbound", "outbound") and raw_port > 0
        port = raw_port if known else None
        if known:
            src, dst = (peer_node, host_node) if inbound else (host_node, peer_node)
            src_ip = r["peer_ip"] if inbound else r["host_ip"]
        else:
            src, dst = sorted((host_node, peer_node))
            src_ip = None
        if root not in (src, dst):
            summary["other_node"] += 1             # 共用 IP 解析到別台（或模稜兩可留成端點），不屬於這個節點
            continue
        if (known and ((direction == "outbound" and src != root) or (direction == "inbound" and dst != root))) \
                or (not known and direction != "both"):
            summary["other_direction"] += 1        # 方向未知只在「雙向」回
            continue
        if ports_f and port not in ports_f:
            summary["port_filtered"] += 1
            continue
        summary["scope_rows"] += 1
        reason = _exclude_reason(ctx, r, src_ip, port, host_node, peer_node)
        if reason:
            summary[reason] += 1
            continue
        summary["shown_rows"] += 1
        eid = f"{src}>{dst}/tcp/{port}" if known else f"{src}~{dst}/tcp/?"
        e = edges.get(eid)
        if e is None:
            e = edges[eid] = {
                "edge_id": eid, "source_id": src, "target_id": dst, "protocol": "tcp",
                "destination_port": port,
                "service": ctx.service_of(dst, port) if known else {"name": None, "basis": None},
                "direction_status": "inferred" if known else "uncertain",
                "direction_basis": "listen_snapshot" if known else "insufficient_data",
                "evidence_kind": "observed_socket", "observations": 0, "source_refs_total": 0,
                "_first": None, "_last": None, "_collectors": set(), "_truncated": False, "source_refs": []}
        e["observations"] += int(r["count"] or 0)
        e["source_refs_total"] += 1
        if r["first_seen"] and (e["_first"] is None or r["first_seen"] < e["_first"]):
            e["_first"] = r["first_seen"]
        if r["last_seen"] and (e["_last"] is None or r["last_seen"] > e["_last"]):
            e["_last"] = r["last_seen"]
        e["_collectors"].add(host_node)
        if ctx.rows_of_ip.get(r["host_ip"], 0) >= ctx.max_rows:
            e["_truncated"] = True
        if len(e["source_refs"]) < MAX_REFS_PER_EDGE:          # 回應大小有上限；總數看 source_refs_total
            e["source_refs"].append({
                "row_id": r["id"], "collector_ip": r["host_ip"], "collector_node_id": host_node,
                "raw_direction": r["direction"], "local_port": r["local_port"] or None,
                "peer_port": r["peer_port"] or None, "observations": int(r["count"] or 0),
                "first_seen": ts_view(r["first_seen"], ctx.legacy_tz),
                "last_seen": ts_view(r["last_seen"], ctx.legacy_tz)})
    out = []
    for eid in sorted(edges):
        e = edges[eid]
        ends = {e["source_id"], e["target_id"]}
        out.append({
            **{k: v for k, v in e.items() if not k.startswith("_")},
            "first_seen": ts_view(e["_first"], ctx.legacy_tz),
            "last_seen": ts_view(e["_last"], ctx.legacy_tz),
            "observation_sides": "both" if ends <= e["_collectors"] else "single",
            "collector_ref": sorted(e["_collectors"]),
            "freshness": "stale" if (e["_last"] or "") < ctx.warn_cutoff else "fresh",
            "filter_reasons": [],
            "partial": e["_truncated"]})
    others = {n for e in out for n in (e["source_id"], e["target_id"]) if n != root and n.startswith("e:")}
    amb = sum(1 for n in others if len(ctx.ident["keys_of_ip"].get(parse_node_id(n)[1], ())) > 1)
    return {"edges": out, "excluded_summary": {**summary, "unresolved_nodes": len(others) - amb,
                                               "ambiguous_nodes": amb}}


def node_view(ctx: _Ctx, node_id: str) -> dict:
    kind, val = parse_node_id(node_id)
    if kind == "host":
        status, reason, detail = ctx.mgmt.get(val, ("unknown", "unknown", None))
        last = ctx.last_of_key.get(val)
        n = ctx.rows_of_key.get(val, 0)
        trunc = any(ctx.rows_of_ip.get(ip, 0) >= ctx.max_rows for ip in ctx.ips_of_key.get(val, ()))
        serials = sorted(ctx.serials_of_key.get(val, []))
        return {
            "node_id": node_id, "entity_type": "host", "machine_key": val,
            "asset_ref": serials[0] if serials else None,
            "display_name": ctx.hostname.get(val),
            "addresses": sorted(ctx.ips_of_key.get(val, set())), "network_scope": NAMESPACE,
            # 業務系統（APID）：畫面在鄰居太多時依它分組；一台掛多個系統的畫面會單獨成框
            "systems": sorted(ctx.ident["apis_of_key"].get(val, set())),
            "management": {"status": status, "reason_code": reason, "source_ref": "manage_state",
                           "checked_at": None, "detail": detail},
            "role": "unknown", "role_source": None,
            "identity": {"status": "resolved", "candidate_count": 1},
            "health": None,
            "coverage": {"last_collection_at": ts_view(last, ctx.legacy_tz),
                         "collection_status": "collected" if n else "not_collected",
                         "row_count": n, "truncated": trunc,
                         "reason": "row_limit_reached" if trunc else (None if n else "no_observation_from_this_host")}}
    keys = ctx.ident["keys_of_ip"].get(val, set())
    amb = len(keys) > 1
    return {
        "node_id": node_id, "entity_type": "endpoint", "machine_key": None, "asset_ref": None,
        "display_name": None, "addresses": [val], "network_scope": NAMESPACE, "systems": [],
        "management": ({"status": "unknown", "reason_code": "ambiguous_identity", "source_ref": "inventory",
                        "checked_at": None, "detail": None} if amb else
                       {"status": "unmanaged", "reason_code": "not_in_inventory", "source_ref": "inventory",
                        "checked_at": None, "detail": None}),
        "role": "unknown", "role_source": None,
        "identity": {"status": "ambiguous" if amb else "unresolved", "candidate_count": len(keys)},
        "health": None,
        "coverage": {"last_collection_at": None, "collection_status": "not_collected", "row_count": 0,
                     "truncated": False, "reason": "endpoint_one_sided"}}


# ── snapshot 與 cursor ────────────────────────────────────────────────

_SECRET = secrets.token_bytes(32)
_LOCK = threading.Lock()
_SNAPS: "OrderedDict[str, dict]" = OrderedDict()


def _reset_snapshots() -> None:
    """測試用：清掉全部 snapshot。"""
    with _LOCK:
        _SNAPS.clear()
        _CTX_CACHE.clear()


_CTX_CACHE: dict = {}


def _ctx_for(conn, sig: tuple) -> _Ctx:
    """同一個資料版本共用一份 _Ctx（身分、納管狀態、涵蓋統計很貴，不要每展開一個節點就重算）。
    建完再核一次版本：建的途中資料變了，這份 ctx 就混了兩個版本 → 409 請使用者重新讀取。"""
    with _LOCK:
        hit = _CTX_CACHE.get("v")
        if hit and hit[0] == sig:
            return hit[1]
    ctx = _Ctx(conn)
    if data_signature(conn) != sig:
        raise SnapshotStale("資料已更新，請重新讀取")
    with _LOCK:
        _CTX_CACHE["v"] = (sig, ctx)
    return ctx


def _now_mono() -> float:
    return time.monotonic()


def _get_snapshot(sid: str) -> dict | None:
    with _LOCK:
        s = _SNAPS.get(sid)
        if s is None:
            return None
        if _now_mono() - s["last_used"] > SNAPSHOT_TTL_SEC:
            _SNAPS.pop(sid, None)
            return None
        s["last_used"] = _now_mono()
        _SNAPS.move_to_end(sid)
        return s


def _new_snapshot(sig: tuple) -> dict:
    with _LOCK:
        sid = secrets.token_urlsafe(12)
        s = {"id": sid, "sig": sig, "created": datetime.now(), "last_used": _now_mono(), "results": OrderedDict()}
        _SNAPS[sid] = s
        while len(_SNAPS) > MAX_SNAPSHOTS:
            _SNAPS.popitem(last=False)
        return s


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _make_cursor(sid: str, qkey: str, offset: int) -> str:
    body = _b64(json.dumps({"s": sid, "q": hashlib.sha256(qkey.encode()).hexdigest()[:32], "o": offset},
                           separators=(",", ":")).encode())
    sig = _b64(hmac.new(_SECRET, body.encode(), hashlib.sha256).digest()[:16])
    return f"{body}.{sig}"


def _read_cursor(cursor: str) -> dict:
    if not isinstance(cursor, str) or len(cursor) > 512 or cursor.count(".") != 1:
        raise BadCursor("cursor 無效")
    body, sig = cursor.split(".")
    want = _b64(hmac.new(_SECRET, body.encode(), hashlib.sha256).digest()[:16])
    if not hmac.compare_digest(want, sig):
        raise BadCursor("cursor 無效")
    try:
        p = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError) as exc:
        raise BadCursor("cursor 無效") from exc
    if not (isinstance(p, dict) and isinstance(p.get("s"), str) and isinstance(p.get("o"), int) and p["o"] >= 0):
        raise BadCursor("cursor 無效")
    return p


# ── 對外 ──────────────────────────────────────────────────────────────

def normalize_filters(protocol=None, port=None, evidence_kind=None) -> dict:
    out: dict = {}
    if protocol is not None:
        if protocol not in PROTOCOLS:
            raise BadParam("protocol 只接受 tcp")
        out["protocol"] = protocol
    if port:
        ports = sorted(set(port))
        if len(ports) > MAX_PORT_FILTERS or any(not (1 <= p <= 65535) for p in ports):
            raise BadParam("port 要在 1～65535，最多 20 個")
        out["port"] = ports
    if evidence_kind is not None:
        if evidence_kind not in EVIDENCE_KINDS:
            raise BadParam("evidence_kind 不認得")
        out["evidence_kind"] = evidence_kind
    return out


def neighbors(conn, node_id: str, direction: str = "outbound", limit: int = DEFAULT_LIMIT,
              cursor: str | None = None, snapshot_id: str | None = None, filters: dict | None = None,
              request_id: str | None = None) -> dict:
    if not enabled(conn):
        raise NotEnabled("功能未開啟")
    if direction not in DIRECTIONS:
        raise BadParam("direction 只接受 outbound／inbound／both")
    if not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise BadParam(f"limit 要在 1～{MAX_LIMIT}")
    kind, val = parse_node_id(node_id)
    filters = filters or {}
    qkey = json.dumps({"n": node_id, "d": direction, "f": filters}, sort_keys=True, ensure_ascii=False)
    offset = 0
    if cursor:
        p = _read_cursor(cursor)
        if p["q"] != hashlib.sha256(qkey.encode()).hexdigest()[:32] or (snapshot_id and snapshot_id != p["s"]):
            raise BadCursor("cursor 跟查詢條件不符")
        snapshot_id, offset = p["s"], p["o"]
    sig = None
    if snapshot_id:
        snap = _get_snapshot(snapshot_id)
        if snap is None:
            raise SnapshotExpired("資料版本已過期，請重新讀取")
    else:
        sig = data_signature(conn)
        snap = _new_snapshot(sig)
    result = snap["results"].get(qkey)
    if result is None:
        if cursor:
            raise SnapshotExpired("資料版本已過期，請重新讀取")
        sig = sig or data_signature(conn)
        if sig != snap["sig"]:
            raise SnapshotStale("資料已更新，請重新讀取")
        ctx = _ctx_for(conn, sig)
        if kind == "host" and not ctx.live_key(val):
            raise NodeNotFound("找不到這個節點")
        full = compute(conn, ctx, node_id, direction, filters)
        ids = {node_id} | {n for e in full["edges"] for n in (e["source_id"], e["target_id"])}
        full["nodes"] = {n: node_view(ctx, n) for n in sorted(ids)}
        full["root_coverage"] = full["nodes"][node_id]["coverage"]
        full["warnings"] = [w for w in (ctx.mgmt_warning,) if w]
        if kind == "endpoint":
            rid, cnt = ctx.resolve_ip(val)
            if cnt == 1:
                full["warnings"].append("identity_resolved")
                full["resolved_as"] = rid
        if any(e["freshness"] == "stale" for e in full["edges"]):
            full["warnings"].append("stale_data")
        full["stale_warn_days"] = ctx.warn_days
        with _LOCK:
            snap["results"][qkey] = full
            while len(snap["results"]) > MAX_RESULTS_PER_SNAPSHOT:
                snap["results"].popitem(last=False)
        result = full
    edges = result["edges"]
    page = edges[offset:offset + limit]
    has_more = offset + limit < len(edges)
    page_nodes = {node_id} | {n for e in page for n in (e["source_id"], e["target_id"])}
    reasons = []
    cov = result["root_coverage"]
    if kind == "endpoint":
        reasons.append("endpoint_one_sided")
    elif cov["collection_status"] == "not_collected":
        reasons.append("root_not_collected")
    if cov["truncated"] or any(e["partial"] for e in edges):
        reasons.append("source_truncated")
    if "management_unavailable" in result["warnings"]:
        reasons.append("management_unavailable")
    status = "partial" if reasons else "complete"
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snap["id"],
        "generated_at": snap["created"].strftime(TS_FMT),
        "root_node_id": node_id,
        "direction": direction,
        "filters": filters,
        "nodes": [result["nodes"][n] for n in sorted(page_nodes)],
        "edges": page,
        "total_edges": len(edges),
        "next_cursor": _make_cursor(snap["id"], qkey, offset + limit) if has_more else None,
        "has_more": has_more,
        "completeness": {"status": status, "reasons": reasons, "truncated": "source_truncated" in reasons},
        "coverage": cov,
        "excluded_summary": result["excluded_summary"],
        "warnings": list(result["warnings"]),
        "resolved_as": result.get("resolved_as"),
        "stale_warn_days": result["stale_warn_days"],
        "request_id": request_id,
    }
