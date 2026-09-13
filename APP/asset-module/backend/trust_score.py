"""資料可信度評分：每台資產有幾個獨立來源互相印證、有沒有機器端的證據。

使用者 2026-09-11 定的規則（對話拍板，不要自己改配分）：

## 來源分（滿分 70）——只算「這台適用」的來源，照比例給分
| 來源 | 配分 | 怎麼判定這台「有」 |
|---|---|---|
| dynassets（存活清單） | 25 | source_record 有 source='dynassets' 對到這台 |
| RVTools（vCenter） | 25 | source_record 有 source='vcenter' 對到這台；ESXi 主機看 vHost 名稱 |
| CIA 資產清冊 | 20 | 資產序號不是 DYN-/VC-/AUTO- 開頭（跟頭條「在管資產」同一條規則，2026-09-07） |

RVTools 只列 VM（和 ESXi 主機本身），所以**實體機的 RVTools 不適用**——
照固定配分實體機永遠拿不到滿分，那是使用者自己抓到的「思考 BUG」。
型態不明（沒有機型）的**照 VM 算**（分母較大、給低分），寧可保守。

## 機器證據（取最高一項，不疊加）
- 網路通＋20：近期掃描或服務採集看得到，**或者有在 dynassets 裡**
  （使用者 2026-09-11 拍板：dynassets 本身就是 Satellite＋nmap 的存活探測清單，
  列在上面＝匯出當時是活的）。依據要分開記、畫面要講——
  「我們自己掃到」跟「匯入的存活清單說它活著（匯入日期 X）」可信程度不一樣。
- 已納管（collect_ok=1，系統連得進去自己收）＋30

## 特例
- 三個來源都沒有、只有網路上看得到 → 固定 10 分。

## 錨點（使用者給的，測試盯著）
- 三個來源＋已納管＝100；三個來源＋網路通＝90；dynassets＋RVTools 沒有 CIA（＋網路通）＝70。

## 第一版的「正確」＝這個來源有這台
欄位對不上（IP／主機名／OS 不同）**只列警示、不扣分**——使用者同意先這樣，
看過警示多不多再決定要不要扣。

## 為什麼 is_vm 不能單獨拿來分 VM／實體
221 實測：is_vm=0 的 2,665 台裡有 1,158 台機型寫的是「(VM)」——is_vm 預設 0，
0 同時代表「實體」和「沒填」。所以型態看「is_vm 或機型含 VM/Virtual/VMware」。
"""
from __future__ import annotations

import re

SRC_WEIGHT = {"dynassets": 25, "rvtools": 25, "cia": 20}
SRC_MAX = 70
BONUS_ALIVE = 20
BONUS_MANAGED = 30
PING_ONLY = 10

OFF_BOOK_PREFIX = ("DYN-", "VC-", "AUTO-")
_VM_MODEL = re.compile(r"\(vm\)|vmware|virtual|kvm|hyper-?v|xen", re.I)

KIND_LABEL = {"vm": "VM", "physical": "實體機", "esxi": "ESXi 主機", "unknown": "型態不明（照 VM 算）"}


def _short(name: str | None) -> str:
    """主機名比對用：小寫、去網域。ESXi 在 RVTools 常是 FQDN，資產表多半是短名。"""
    return (name or "").strip().lower().split(".")[0]


def classify_kind(is_vm, device_model, hostname, esxi_names: set[str]) -> str:
    if _short(hostname) and _short(hostname) in esxi_names:
        return "esxi"
    v = str(is_vm or "").strip().lower()
    if v not in ("", "0", "none", "false", "否"):
        return "vm"
    model = (device_model or "").strip()
    if not model:
        return "unknown"
    return "vm" if _VM_MODEL.search(model) else "physical"


def applicable_sources(kind: str) -> tuple[str, ...]:
    return ("dynassets", "cia") if kind == "physical" else ("dynassets", "rvtools", "cia")


def score_one(kind: str, has: dict[str, bool], alive: bool, managed: bool) -> dict:
    """單台的分數與拆解。純函式，測試直接打它。"""
    app = applicable_sources(kind)
    got = [s for s in app if has.get(s)]
    denom = sum(SRC_WEIGHT[s] for s in app)
    src_score = round(SRC_MAX * sum(SRC_WEIGHT[s] for s in got) / denom) if denom else 0
    if managed:
        bonus, evidence = BONUS_MANAGED, "managed"
    elif alive:
        bonus, evidence = BONUS_ALIVE, "alive"
    else:
        bonus, evidence = 0, None
    if not got:
        # 三個來源都沒有：只看得到網路 → 10；連網路都沒有 → 0；已納管的照加分規則
        score = PING_ONLY if evidence == "alive" else bonus
    else:
        score = src_score + bonus
    return {
        "score": min(score, 100), "source_score": src_score if got else 0,
        "evidence": evidence, "kind": kind,
        "sources": {s: (bool(has.get(s)) if s in app else None) for s in SRC_WEIGHT},
    }


def _source_sets(conn) -> tuple[set[int], set[int], set[str]]:
    dyn, vc = set(), set()
    for r in conn.execute(
            "SELECT DISTINCT source, resolved_hardware_id FROM source_record "
            "WHERE source IN ('dynassets','vcenter') AND resolved_hardware_id IS NOT NULL"):
        (dyn if r[0] == "dynassets" else vc).add(r[1])
    esxi: set[str] = set()
    import json
    for r in conn.execute("SELECT payload FROM source_record WHERE source = 'vcenter_extra:vHost'"):
        try:
            host = json.loads(r[0]).get("Host")
        except (ValueError, TypeError):
            continue
        if host:
            esxi.add(_short(host))
    return dyn, vc, esxi


def dynassets_imported_at(conn) -> str | None:
    """dynassets 最近一次匯入的時間——「存活清單說它活著」要講是哪一天的清單。"""
    try:
        row = conn.execute(
            "SELECT MAX(collected_at) FROM source_record WHERE source = 'dynassets'").fetchone()
    except Exception:  # noqa: BLE001
        return None
    return row[0] if row else None


def compute_all(conn) -> dict[str, dict]:
    """全部非退役資產的分數：{asset_serial: {...}}。一次查完，逐台不打 SQL。"""
    import data_quality
    import manage_state as ms

    dyn, vc, esxi = _source_sets(conn)
    seen = data_quality._seen_ips(conn)
    dyn_at = dynassets_imported_at(conn)
    ph = ",".join("?" for _ in ms.RETIRED_STATUS)
    out: dict[str, dict] = {}
    for a in conn.execute(
            "SELECT id, asset_serial, hostname, ip, is_vm, device_model, collect_ok "
            f"FROM hardware WHERE COALESCE(asset_status,'') NOT IN ({ph})",
            tuple(ms.RETIRED_STATUS)):
        kind = classify_kind(a["is_vm"], a["device_model"], a["hostname"], esxi)
        has = {
            "dynassets": a["id"] in dyn,
            # ESXi 主機本身在 RVTools 的 vHost 分頁，不在 vInfo——名字對得到就算有
            "rvtools": a["id"] in vc or kind == "esxi",
            "cia": not str(a["asset_serial"] or "").startswith(OFF_BOOK_PREFIX),
        }
        seen_by_us = (a["ip"] or "").strip() in seen
        r = score_one(kind, has, seen_by_us or has["dynassets"], a["collect_ok"] == 1)
        # 網路通的依據：自己掃到的優先講（那是我們的證據）；只有 dynassets 說活著就講匯入日期
        if r["evidence"] == "alive":
            r["alive_basis"] = "scan" if seen_by_us else "dynassets"
        else:
            r["alive_basis"] = None
        r["dynassets_imported_at"] = dyn_at
        r.update({"asset_serial": a["asset_serial"], "hostname": a["hostname"], "ip": a["ip"]})
        out[a["asset_serial"]] = r
    return out


_SRC_SHOW = {"dynassets": "dynassets", "rvtools": "RVTools", "cia": "CIA"}
_KIND_SHORT = {"vm": "VM", "physical": "實體機", "esxi": "ESXi 主機", "unknown": "型態不明"}


def explain(r: dict) -> str:
    """一台的分數怎麼來的，一行字：「VM・dynassets＋RVTools＋CIA 70＋網路通(dynassets) 20」。

    使用者 2026-09-11：「分數多一個欄位，說明分數怎麼定義」。同一個分數可能由不同組合湊出來
    （例：實體機 dynassets＋CIA 也是 70），所以分布表每一列列出「是哪幾種組合、各幾台」。
    """
    got = [_SRC_SHOW[k] for k, v in r["sources"].items() if v]
    if got:
        base = f"{'＋'.join(got)} {r['source_score']}"
    else:
        base = "三個來源都沒有"
    if r["evidence"] == "managed":
        ev = f"＋已納管 {BONUS_MANAGED}"
    elif r["evidence"] == "alive":
        why = "dynassets" if r.get("alive_basis") == "dynassets" else "掃描"
        ev = f"＋網路通({why}) {BONUS_ALIVE}" if got else f"＝只看得到網路 {PING_ONLY}"
    else:
        ev = "（沒有機器證據）"
    return f"{_KIND_SHORT.get(r['kind'], r['kind'])}・{base}{ev}"


def distribution(conn) -> dict:
    """分數分布（100 分幾台、90 分幾台…）＋來源組合分布。每個數字都要能點進去。
    每個分數附「怎麼來的」：湊出這個分數的組合各幾台（加總＝該分數台數，測試盯著）。"""
    allr = compute_all(conn)
    by_score: dict[int, int] = {}
    reasons: dict[int, dict[str, int]] = {}
    by_combo: dict[str, int] = {}
    for r in allr.values():
        by_score[r["score"]] = by_score.get(r["score"], 0) + 1
        rs = reasons.setdefault(r["score"], {})
        e = explain(r)
        rs[e] = rs.get(e, 0) + 1
        combo = "+".join(k for k, v in r["sources"].items() if v) or "（三個來源都沒有）"
        by_combo[combo] = by_combo.get(combo, 0) + 1
    return {
        "total": len(allr),
        "by_score": [
            {"score": s, "count": n,
             "reasons": [{"text": t, "count": c}
                         for t, c in sorted(reasons[s].items(), key=lambda x: -x[1])]}
            for s, n in sorted(by_score.items(), reverse=True)],
        "by_combo": [{"combo": c, "count": n} for c, n in sorted(by_combo.items(), key=lambda x: -x[1])],
        "rule": "來源滿分 70（dynassets 25／RVTools 25／CIA 20，只算適用的）＋機器證據取最高"
                "（網路通 +20：我們掃描看到或 dynassets 存活清單有它／已納管 +30）；"
                "三個來源都沒有、只看得到網路＝10",
        "dynassets_imported_at": dynassets_imported_at(conn),
    }


def hosts_with(conn, score: int | None = None, combo: str | None = None) -> list[dict]:
    rows = list(compute_all(conn).values())
    if score is not None:
        rows = [r for r in rows if r["score"] == score]
    if combo is not None:
        rows = [r for r in rows
                if ("+".join(k for k, v in r["sources"].items() if v) or "（三個來源都沒有）") == combo]
    return rows
