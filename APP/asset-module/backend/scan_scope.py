"""掃描範圍：以網段配置表為準，並記錄每次實際涵蓋了哪些網段（2026-09-16）。

## 為什麼要有這支

使用者問「你要怎麼知道要掃描那些網段」。查證結果（221 正式庫）：

- `network_segment` 已有 **184 段**（公司總分公司網段配置表匯入），182 段解析得出 CIDR
- 其中 **95 段**被資安在弱掃說明標「建議排除掃描」（員工電腦／UAT／重複 IP）
- **但掃描排程根本沒用這份表**：`run_real_scan.scan_targets()` 讀的是 `connections`
  （要人工新增「網路掃描」來源），而那張表是 **0 筆** → fallback 只掃本機一段

這就是「失聯 8062 台」的真因：不是那些機器不見了，是**根本沒掃到它們**。
使用者 ping 得通的 `10.92.194.65` 被判失聯，就是這麼來的。

## 這支做兩件事

### 1. 把網段配置表接上掃描範圍

`list_scope()` 列出全部網段（**不幫使用者排除**——使用者 2026-09-15 明講
「不要幫我排除，你可以建議，但我自己選」，所以「建議排除」只是標記），
`apply_scope()` 把勾選的段同步成 `connections` 列。

**沿用既有的 connections 而不是另開一張表**：掃描引擎、單網段排程、啟用停用、
上次掃描時間都已經長在那上面，另開一套就會有兩份「要掃哪裡」而且遲早不一致。

沒被勾的**停用**而不是刪除：刪掉就沒人記得曾經掃過它，`last_scan_at` 也跟著不見。

### 2. 記錄每次掃描涵蓋了哪些網段

`record_coverage()` 在每次掃完寫下這次涵蓋的 CIDR。有了它才分得出：

| 情況 | 以前 | 現在 |
|---|---|---|
| 掃了這段、這台沒回應 | 失聯 | 失聯（正確） |
| 這段根本沒掃 | **失聯（錯的）** | 未涵蓋（沒掃過） |

「找到 0 台」必須分得出「掃了但真的沒有」與「根本沒掃」——否則只給數字等於要人猜。
"""
from __future__ import annotations

import ipaddress
import json
from datetime import datetime

import db

COVERAGE_SQL = """
CREATE TABLE IF NOT EXISTS scan_coverage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_time  TEXT NOT NULL,      -- 對應 scan_history.scan_time
    cidr       TEXT NOT NULL,      -- 這次掃描涵蓋的網段
    source     TEXT,               -- 來源名稱（connections.name）
    ok         INTEGER DEFAULT 1,  -- 0＝這段掃描失敗，不能當成「掃過了」
    created_at TEXT NOT NULL
)
"""

#: connections.connection_type 用這個字串代表「從網段配置表同步過來的掃描來源」
SCAN_TYPE = "網路掃描"
#: 名稱前綴，讓人一眼看出是系統同步的、不是手動加的
NAME_PREFIX = "網段"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure(conn) -> None:
    conn.execute(COVERAGE_SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_coverage_time ON scan_coverage(scan_time)")


# ===== 規則式範圍（2026-09-17 使用者定案「方案 A」）=====
#
# 為什麼從「184 列逐段打勾」改成規則：打勾是「某一刻的快照」，重匯網段配置表、
# 公司多開一個正式伺服器段之後，那段沒人勾 → 整批機器被判「未涵蓋」——正是當初
# 「8062 台假失聯」的坑，只是延到下次 Excel 更新才發作。規則（收哪些類別×環境）
# 是「設一次、之後新段自動歸位」，才真的把坑填掉。
#
# 決策軸是 `category`（SERVER／OA／NETWORK…）＋`environment`，不是逐段。221 實際：
# SERVER 49 段（一段都沒被資安標排除）、OA 員工電腦 50 段（多半建議排除）。
#
# 個別網段永遠能 force_in／force_out 覆寫規則（記 CIDR，重匯不會掉）。
# manual 模式保留舊行為（逐段勾 connections），當逃生口。

POLICY_KEY = "scan_scope_policy"
DEFAULT_POLICY = {
    "mode": "rule",                        # rule＝規則；manual＝逐段勾（舊行為）
    "categories": ["SERVER", "UAT-SERVER"],  # 要收集的類別（空＝不限）
    "environments": ["正式", "測試"],        # 要收集的環境（空＝不限）
    # 2026-09-19 使用者「UAT 伺服器收」：那 29 段 UAT-SERVER 被標建議排除的原因是「UAT環境」，
    # 不是個別資安敏感——盤點要收 UAT 伺服器。預設改 False（SERVER＋UAT-SERVER 全收＝79 段）；
    # 真有個別要排除的用 force_out。要重新尊重資安建議排除就把這個開回 True。
    "respect_recommended_exclude": False,   # 是否尊重資安「建議排除」（可用 force_in／force_out 覆寫）
    "force_in": [],                         # 一定要掃的 CIDR（覆寫規則）
    "force_out": [],                        # 一定不掃的 CIDR（最高優先）
}


def get_policy(conn) -> dict:
    """讀掃描範圍規則；沒設過或壞掉一律回預設（不讓壞 JSON 讓整頁掛掉）。"""
    raw = db.get_setting(conn, POLICY_KEY)
    out = dict(DEFAULT_POLICY)
    if raw:
        try:
            p = json.loads(raw)
            out.update({k: p[k] for k in DEFAULT_POLICY if k in p})
        except (ValueError, TypeError):
            pass  # 壞掉就用預設——這是設定，不是資料，回退比報錯合理
    out["mode"] = "manual" if out.get("mode") == "manual" else "rule"
    out["categories"] = [str(x).strip() for x in (out.get("categories") or []) if str(x).strip()]
    out["environments"] = [str(x).strip() for x in (out.get("environments") or []) if str(x).strip()]
    out["force_in"] = [str(x).strip() for x in (out.get("force_in") or []) if str(x).strip()]
    out["force_out"] = [str(x).strip() for x in (out.get("force_out") or []) if str(x).strip()]
    out["respect_recommended_exclude"] = bool(out.get("respect_recommended_exclude", True))
    return out


def set_policy(conn, policy: dict) -> dict:
    """存規則並**立刻**把掃描來源同步成規則的結果（存了就生效，不用再按套用）。"""
    cur = get_policy(conn)
    cur.update({k: policy[k] for k in DEFAULT_POLICY if k in policy})
    db.set_setting(conn, POLICY_KEY, json.dumps(cur, ensure_ascii=False))
    out = get_policy(conn)
    apply_policy(conn)
    return out


def _decide(seg: dict, policy: dict) -> tuple[bool, str]:
    """單一網段照規則判「在/不在」＋原因。force_out ＞ force_in ＞ 規則。"""
    cidr = seg.get("cidr")
    if not cidr:
        return False, "網段寫法無法解析"
    if cidr in policy["force_out"]:
        return False, "手動強制排除"
    if cidr in policy["force_in"]:
        return True, "手動強制納入"
    cat = (seg.get("category") or "").strip()
    if policy["categories"] and cat not in policy["categories"]:
        return False, f"類別不收集（{cat or '未填'}）"
    env = (seg.get("environment") or "").strip()
    if policy["environments"] and env not in policy["environments"]:
        return False, f"環境不收集（{env or '未填'}）"
    if policy["respect_recommended_exclude"] and seg.get("scan_excluded"):
        return False, "資安建議排除（可個別強制納入）"
    return True, "規則納入"


def resolve_scope(conn, policy: dict | None = None) -> list[dict]:
    """把規則套到每個網段，回傳逐段的 in_scope＋原因（rule 模式才有意義）。"""
    policy = policy or get_policy(conn)
    out = []
    for s in _seg_rows(conn):
        ok, reason = _decide(s, policy)
        out.append({
            "id": s["id"], "cidr": s["cidr"], "category": s["category"],
            "environment": s["environment"], "location": s["location"],
            "recommended_exclude": bool(s["scan_excluded"]),
            "addresses": _size(s["cidr"]), "in_scope": ok, "reason": reason,
        })
    return out


def apply_policy(conn) -> dict:
    """依規則重算 → 同步掃描來源（connections）。manual 模式不動（交給逐段勾）。

    這支就是「自動維護」：網段配置表重匯後呼叫它，新符合規則的段自動納入、
    不再符合的自動停用，不用人回來重勾。
    """
    policy = get_policy(conn)
    if policy["mode"] == "manual":
        return {"mode": "manual", "note": "手動模式，不自動同步"}
    in_ids = [r["id"] for r in resolve_scope(conn, policy) if r["in_scope"]]
    return {"mode": "rule", **apply_scope(conn, in_ids)}


# ===== 範圍設定 =====

def _seg_rows(conn) -> list[dict]:
    cur = conn.execute(
        "SELECT id, cidr, raw_cidr, location, purpose_desc, environment, category, "
        "scan_excluded, scan_note, vlan FROM network_segment ORDER BY id")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _size(cidr: str | None) -> int:
    if not cidr:
        return 0
    try:
        n = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return 0
    return max(n.num_addresses - 2, 1)


def list_scope(conn) -> dict:
    """列出全部網段，標明目前有沒有納入掃描、資安建議、展開後幾個位址。

    ⚠️ **不替使用者過濾**。「建議排除掃描」只是 `recommended_exclude` 這個旗標，
    要不要掃由使用者自己勾（2026-09-15 使用者：「不要幫我排除，你可以建議，但我自己選」）。
    """
    enabled_targets, disabled_targets = _current_targets(conn)
    times = _custom_times(conn)
    policy = get_policy(conn)
    rule = policy["mode"] == "rule"
    # ⚠️ in_scope 永遠＝connections（實際會被掃的來源），兩種模式都一樣——這是唯一真話，
    #    畫面上「在掃描內」必須等於「掃描引擎真的會掃」。rule 模式另外給 rule_in_scope＋reason
    #    （規則會不會納入、為什麼），並用 in_sync 標「規則跟實際來源是否已同步」。
    decided = {s["id"]: _decide(s, policy) for s in _seg_rows(conn)} if rule else {}
    items = []
    for s in _seg_rows(conn):
        cidr = s["cidr"]
        in_scope = bool(cidr and cidr in enabled_targets)
        rule_in, reason = decided.get(s["id"], (None, "手動模式（逐段勾選）")) if rule else (None, "手動勾選")
        items.append({
            "id": s["id"],
            "cidr": cidr,
            "raw_cidr": s["raw_cidr"],
            "parsable": bool(cidr),
            "location": s["location"],
            "purpose": s["purpose_desc"],
            "environment": s["environment"],
            "category": s["category"],
            "vlan": s["vlan"],
            "recommended_exclude": bool(s["scan_excluded"]),
            "exclude_note": s["scan_note"],
            "addresses": _size(cidr),
            "in_scope": in_scope,                   # 實際會掃（connections）
            "rule_in_scope": rule_in,               # 規則說要不要掃（rule 模式才有）
            "reason": reason,                        # 為什麼（畫面要看得出來）
            "was_in_scope": bool(cidr and cidr in disabled_targets),
            # 自訂時間併進同一列（沒設＝跟全域排程一起），畫面才不用列兩張表
            "connection_id": (times.get(cidr) or {}).get("connection_id"),
            "scan_time": (times.get(cidr) or {}).get("scan_time"),
            "last_scan_at": (times.get(cidr) or {}).get("last_scan_at"),
        })
    chosen = [i for i in items if i["in_scope"]]
    rule_chosen = [i for i in items if i["rule_in_scope"]]
    # 規則跟實際來源是否一致（不一致＝規則改了還沒按套用同步）
    in_sync = (not rule) or ({i["cidr"] for i in rule_chosen} == {i["cidr"] for i in chosen})
    return {
        "items": items,
        "policy": policy,
        "total_segments": len(items),
        "unparsable": sum(1 for i in items if not i["parsable"]),
        "recommended_exclude": sum(1 for i in items if i["recommended_exclude"]),
        "in_scope_segments": len(chosen),
        "in_scope_addresses": sum(i["addresses"] for i in chosen),
        "rule_in_scope_segments": len(rule_chosen),
        "rule_in_scope_addresses": sum(i["addresses"] for i in rule_chosen),
        "in_sync": in_sync,
    }


def _current_targets(conn) -> tuple[set[str], set[str]]:
    """目前的掃描來源：(啟用的 target, 停用的 target)。只看系統同步出來的那些。"""
    enabled, disabled = set(), set()
    for r in conn.execute(
            "SELECT target, enabled FROM connections WHERE connection_type = ?", (SCAN_TYPE,)):
        (enabled if (r[1] is None or r[1]) else disabled).add((r[0] or "").strip())
    return enabled, disabled


def _custom_times(conn) -> dict[str, dict]:
    """每個網段自己的掃描時間（沒設＝跟全域排程一起掃）。

    2026-09-17 使用者：「看這兩個表要不要整合在一起？我會認為上面的打勾了就是已經啟動了，
    我根本不會再去看它自己掃描的時間是什麼。」——他是對的，打勾就是啟用；
    自訂時間只是讓某些網段錯開、不要擠在同一時刻。兩張表分開列同一批網段會讓人
    以為下面那張是必填。所以把時間併進同一份資料，畫面就能合成一張表。
    """
    out = {}
    for r in conn.execute(
            "SELECT id, target, scan_time, last_scan_at FROM connections "
            "WHERE connection_type = ?", (SCAN_TYPE,)):
        out[(r[1] or "").strip()] = {
            "connection_id": r[0],
            "scan_time": (r[2] or "").strip() or None,
            "last_scan_at": r[3],
        }
    return out


def apply_scope(conn, segment_ids: list[int], by: str | None = None) -> dict:
    """把勾選的網段同步成掃描來源。沒勾的停用（不刪，保留上次掃描時間與紀錄）。"""
    wanted: dict[str, dict] = {}
    for s in _seg_rows(conn):
        if s["id"] in set(segment_ids or []) and s["cidr"]:
            wanted[s["cidr"]] = s
    now = _now()
    added, re_enabled, disabled = [], [], []

    existing = {}
    for r in conn.execute(
            "SELECT id, target, enabled FROM connections WHERE connection_type = ?", (SCAN_TYPE,)):
        existing[(r[1] or "").strip()] = {"id": r[0], "enabled": r[2]}

    for cidr, seg in wanted.items():
        cur = existing.get(cidr)
        if cur is None:
            name = f"{NAME_PREFIX} {cidr}" + (f"（{seg['location']}）" if seg["location"] else "")
            conn.execute(
                "INSERT INTO connections (name, connection_type, target, enabled, created_at, updated_at) "
                "VALUES (?,?,?,1,?,?)", (name[:120], SCAN_TYPE, cidr, now, now))
            added.append(cidr)
        elif not (cur["enabled"] is None or cur["enabled"]):
            conn.execute("UPDATE connections SET enabled = 1, updated_at = ? WHERE id = ?", (now, cur["id"]))
            re_enabled.append(cidr)

    for cidr, cur in existing.items():
        if cidr not in wanted and (cur["enabled"] is None or cur["enabled"]):
            conn.execute("UPDATE connections SET enabled = 0, updated_at = ? WHERE id = ?", (now, cur["id"]))
            disabled.append(cidr)

    conn.commit()
    return {"added": added, "re_enabled": re_enabled, "disabled": disabled,
            "in_scope": sorted(wanted), "addresses": sum(_size(c) for c in wanted)}


# ===== 涵蓋範圍紀錄 =====

def record_coverage(conn, scan_time: str, sources) -> int:
    """掃完記下這次涵蓋了哪些網段。`sources` 是 ScanSource 清單（有 cidr／name）。

    掃描失敗的那一段記 ok=0——失敗不等於掃過了，那段裡的機器不該被判成失聯。
    """
    _ensure(conn)
    n = 0
    now = _now()
    for s in sources:
        cidr = (getattr(s, "cidr", None) or getattr(s, "target", None) or "").strip()
        if not cidr:
            continue
        conn.execute(
            "INSERT INTO scan_coverage (scan_time, cidr, source, ok, created_at) VALUES (?,?,?,?,?)",
            (scan_time, cidr, getattr(s, "name", None), 1, now))
        n += 1
    conn.commit()
    return n


def mark_failed(conn, scan_time: str, source_name: str) -> None:
    """某一段掃描失敗 → 標 ok=0，之後不算「掃過了」。"""
    _ensure(conn)
    conn.execute("UPDATE scan_coverage SET ok = 0 WHERE scan_time = ? AND source = ?",
                 (scan_time, source_name))
    conn.commit()


def covered_networks(conn, scan_time: str | None):
    """這次掃描成功涵蓋的網段（ipaddress 物件清單）。沒有紀錄回空——
    空的意思是「不知道涵蓋了什麼」，呼叫端要當成「無法判斷」而不是「什麼都沒涵蓋」。"""
    if not scan_time:
        return []
    try:
        _ensure(conn)
        rows = conn.execute(
            "SELECT DISTINCT cidr FROM scan_coverage WHERE scan_time = ? AND ok = 1",
            (scan_time,)).fetchall()
    except Exception:  # noqa: BLE001 - 舊庫沒這張表就當沒有紀錄
        return []
    nets = []
    for r in rows:
        try:
            nets.append(ipaddress.ip_network(r[0], strict=False))
        except ValueError:
            continue
    return nets


def is_covered(ip: str | None, nets) -> bool:
    """這個 IP 有沒有被這次掃描涵蓋到。沒有涵蓋紀錄時一律回 True——
    寧可維持舊行為（判失聯），也不要在沒有證據的情況下把全部機器改標成「未涵蓋」。"""
    if not nets:
        return True
    if not ip:
        return True          # 沒有 IP 的機器本來就不是靠掃描判定
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return True
    return any(addr in n for n in nets)
