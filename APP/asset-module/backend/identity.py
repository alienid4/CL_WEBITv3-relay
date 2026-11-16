"""身分解析（Identity Resolution）：這筆來源紀錄，是不是我已經有的那一台？

多來源（Excel／掃描／facts／之後的 vCenter）匯進同一份資產清單時，最關鍵也最容易
出事的一步。合併錯了不會噴錯——只會安靜地把兩台不同機器變成一台，而且很難發現、
更難還原。所以這裡的原則是**寧可留兩筆給人決定，也不要自動合併錯**。

## 為什麼不能用「IP 相符 or hostname 相符」

那是既有 `api._row_in_keys` 的做法，它用在「這台登記的機器有沒有出現在本次掃描」
的**比對計數**上——同一個網段、同一個時間點的快照，風險可控。

但拿它來做**合併**會出事：
  - DHCP 回收 IP：舊機器下線、新機器拿到同一個 IP → 被判成同一台
  - dev/prod 同名主機 → 被合併
  - NAT／多 VRF 下不同機器可以有相同 IP

## 識別碼強度階梯

  最強  vm_uuid     虛擬機唯一識別（vCenter instanceUuid），換 IP 換名都不變
  強    hw_serial   硬體序號（DMI product_serial），facts 已在收
  中    mac         網卡位址（VM 複製可能重複，故不列強）
  弱    hostname    會重複（dev/prod 同名）
  最弱  ip          會被回收再指派

**鐵則**：弱識別碼不單獨定案。強識別碼相符就定案；只有弱的要兩個以上同時相符；
強識別碼互相矛盾一律判 ambiguous 交人工。
"""
from __future__ import annotations

from dataclasses import dataclass, field

STRONG = ("vm_uuid", "hw_serial")
MEDIUM = ("mac",)
WEAK = ("hostname", "ip")

# 判定結果
MATCHED = "matched"      # 確定是既有的那一台
NEW = "new"              # 確定是新的一台
AMBIGUOUS = "ambiguous"  # 判不準——**不自動合併**，進人工審核


@dataclass
class MatchResult:
    status: str
    hardware_id: int | None = None
    rule: str = ""
    confidence: float = 0.0
    candidates: list[dict] = field(default_factory=list)
    reason: str = ""


def _norm(v) -> str | None:
    """識別碼比對前的正規化。大小寫、前後空白、MAC 的分隔符都不該影響判定。"""
    if v is None:
        return None
    s = str(v).strip().lower()
    if not s:
        return None
    return s


def _norm_mac(v) -> str | None:
    s = _norm(v)
    if not s:
        return None
    s = s.replace("-", ":").replace(".", ":")
    return s if len(s.replace(":", "")) == 12 else s


def extract(record: dict) -> dict:
    """從一筆來源紀錄抽出識別碼，統一正規化。"""
    return {
        "vm_uuid": _norm(record.get("vm_uuid")),
        "hw_serial": _norm(record.get("hw_serial")),
        "mac": _norm_mac(record.get("mac")),
        "hostname": _norm(record.get("hostname")),
        "ip": _norm(record.get("ip")),
    }


def resolve(conn, record: dict) -> MatchResult:
    """把一筆來源紀錄解析到既有資產，或判定為新的／模糊。

    回 MatchResult。**呼叫端絕對不可以把 AMBIGUOUS 當成 NEW 或 MATCHED 處理**——
    那正是自動合併錯的來源。ambiguous 要進 merge_review 讓人決定。
    """
    ids = extract(record)
    rows = conn.execute(
        "SELECT id, asset_serial, hostname, ip, mac, hw_serial, vm_uuid FROM hardware"
    ).fetchall()

    # ---- 1) 強識別碼：相符即定案 ----
    for key in STRONG:
        val = ids.get(key)
        if not val:
            continue
        hits = [r for r in rows if _norm(r[key]) == val]
        if len(hits) == 1:
            return MatchResult(MATCHED, hits[0]["id"], f"strong:{key}", 1.0,
                               reason=f"{key} 完全相符")
        if len(hits) > 1:
            # 同一個強識別碼對到多筆＝資料本身已經有問題，不可以隨便挑一個
            return MatchResult(
                AMBIGUOUS, None, f"strong:{key}:duplicate", 0.0,
                candidates=[dict(r) for r in hits],
                reason=f"{key}={val} 對應到 {len(hits)} 筆既有資產，資料有重複需人工確認")

    # ---- 2) 強識別碼互相矛盾：明確不是同一台，但也不敢直接當新的 ----
    for key in STRONG:
        val = ids.get(key)
        if not val:
            continue
        # 弱識別碼指向某台，但那台的強識別碼跟這筆不同 → 是兩台不同機器共用了 IP/名稱
        for r in rows:
            other = _norm(r[key])
            if not other or other == val:
                continue
            if (ids["ip"] and _norm(r["ip"]) == ids["ip"]) or \
               (ids["hostname"] and _norm(r["hostname"]) == ids["hostname"]):
                return MatchResult(
                    AMBIGUOUS, None, f"conflict:{key}", 0.0,
                    candidates=[dict(r)],
                    reason=f"IP／主機名相符但 {key} 不同（{val} vs {other}）"
                           "——很可能是 IP 被回收或同名主機，需人工確認")

    # ---- 3) 中等識別碼（MAC）：相符且沒有強識別碼矛盾 ----
    if ids["mac"]:
        hits = [r for r in rows if _norm_mac(r["mac"]) == ids["mac"]]
        if len(hits) == 1:
            return MatchResult(MATCHED, hits[0]["id"], "medium:mac", 0.85,
                               reason="MAC 相符且無強識別碼矛盾")
        if len(hits) > 1:
            return MatchResult(AMBIGUOUS, None, "medium:mac:duplicate", 0.0,
                               candidates=[dict(r) for r in hits],
                               reason=f"MAC={ids['mac']} 對應到多筆資產")

    # ---- 4) 只剩弱識別碼：必須兩個都相符才算，單一個一律 ambiguous ----
    weak_hits = []
    for r in rows:
        agree = sum(
            1 for k in WEAK if ids.get(k) and _norm(r[k]) == ids[k]
        )
        if agree:
            weak_hits.append((agree, r))

    both = [r for n, r in weak_hits if n >= 2]
    if len(both) == 1:
        return MatchResult(MATCHED, both[0]["id"], "weak:hostname+ip", 0.7,
                           reason="主機名與 IP 同時相符")
    if len(both) > 1:
        return MatchResult(AMBIGUOUS, None, "weak:duplicate", 0.0,
                           candidates=[dict(r) for r in both],
                           reason="主機名與 IP 同時相符的資產有多筆")

    single = [r for n, r in weak_hits if n == 1]
    if single:
        # 這是最危險的一格：只有 IP 或只有主機名相符。
        # 舊做法會直接判成同一台——DHCP 回收 IP、dev/prod 同名都會在這裡出事。
        return MatchResult(AMBIGUOUS, None, "weak:single", 0.0,
                           candidates=[dict(r) for r in single],
                           reason="只有 IP 或只有主機名相符，不足以斷定是同一台"
                                  "（IP 可能被回收、主機名可能重複）")

    return MatchResult(NEW, None, "no-match", 1.0, reason="沒有任何識別碼相符")


# ---- 診斷外掛 ----
try:
    import diagnostics

    @diagnostics.register("identity")
    def _diag(conn) -> dict:
        """身分解析的軌跡：哪條規則、信心度、為什麼判 ambiguous。
        看到「這筆走了 weak:single」比看到資料本身有用十倍。"""
        try:
            staged = [dict(r) for r in conn.execute(
                "SELECT source, source_key, resolved_status, resolved_rule, "
                "resolved_confidence FROM source_record ORDER BY id DESC LIMIT 200")]
            reviews = [dict(r) for r in conn.execute(
                "SELECT id, reason, status, created_at FROM merge_review "
                "WHERE status='open' ORDER BY id DESC LIMIT 50")]
        except Exception:  # noqa: BLE001 - 表還沒建時不該讓整包壞掉
            staged, reviews = [], []
        # 識別碼覆蓋率：弱識別碼比例越高，合併判斷越不可靠——這是關鍵健康指標
        cov = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN vm_uuid IS NOT NULL AND vm_uuid != '' THEN 1 ELSE 0 END) AS has_uuid, "
            "SUM(CASE WHEN hw_serial IS NOT NULL AND hw_serial != '' THEN 1 ELSE 0 END) AS has_serial, "
            "SUM(CASE WHEN mac IS NOT NULL AND mac != '' THEN 1 ELSE 0 END) AS has_mac "
            "FROM hardware").fetchone()
        return {
            "identifier_coverage": dict(cov),
            "staged_recent": staged,
            "open_reviews": reviews,
        }
except ImportError:
    pass
