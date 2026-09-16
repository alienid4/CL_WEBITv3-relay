"""把「卡在人工複核佇列、但其實證據夠強」的那批自動解掉。

## 這支在解什麼問題

2026-08-24 實查 221：`merge_review` 有 3333 筆 open，逐筆拆開後：

    2140  來源是 vCenter、帶著 vm_uuid，而對到的那台資產 vm_uuid 是空的
     610  一筆來源對到多個候選資產
     450  來源是 dynassets（沒有 vm_uuid 這種強識別碼可用）
     133  兩邊 vm_uuid 不同（衝突）

那 2140 筆卡住的原因，是 `identity.resolve()` 的第 1 關（強識別碼相符）比不到——
資產那邊 vm_uuid 還是空的，比什麼都比不到——於是掉到第 4 關「只剩弱識別碼」，
單一個弱識別碼不定案，判 ambiguous。**規則本身沒錯，但它漏了一種情況**：

    來源手上有身分證號，對面那台還沒填身分證號，而且沒有任何人跟這個號碼衝突。

這種情況合併不是冒險，合併正是在**把身分證號補上去**——補完以後，之後每次
vCenter 同步都能走第 1 關精準對上，不會再生出新的待審核。不補的話這個佇列
只會越長越大（治源頭 vs 擦地板）。

## 規則（六個條件全部成立才收，缺一不可）

1. 來源是 vCenter（對 VM 來說它是權威來源）
2. 來源帶 vm_uuid
3. 候選資產**剛好一個**
4. 候選資產的 vm_uuid 是**空的**（不是不同——不同是衝突，那要人看）
5. 弱識別碼至少一個相符（IP 或主機名），確認不是隨便挑一台來塞
6. **這個 vm_uuid 沒有被其他資產佔用**——少了這條會做出兩台資產同一個 uuid，
   下次 `resolve()` 走第 1 關就會撞上「對到多筆」，等於把問題推到未來放大

不符合的一律不碰，留在佇列裡給人看。`plan()` 只算不寫，寫入走 `apply()`。
"""
from __future__ import annotations

import json

import identity

VCENTER = "vcenter"


def _payload(row) -> dict:
    try:
        return json.loads(row["payload"])
    except Exception:  # noqa: BLE001 - 壞掉的 payload 就是不處理，不是讓整批掛掉
        return {}


def _candidate_ids(row) -> list[int]:
    try:
        cands = json.loads(row["candidates"])
    except Exception:  # noqa: BLE001
        return []
    out = []
    for c in cands:
        cid = c.get("id") if isinstance(c, dict) else c
        if isinstance(cid, int):
            out.append(cid)
    return out


def plan(conn) -> dict:
    """算出「可以自動解掉」的清單。**唯讀，不寫任何東西。**

    回 {"resolvable": [...], "skipped": {原因: 筆數}}。
    resolvable 每筆都帶足夠的證據欄位，讓人可以抽驗——只給數字不給依據的話，
    人沒辦法判斷這批能不能信。
    """
    rows = conn.execute(
        "SELECT m.id AS review_id, m.candidates, s.source, s.payload "
        "FROM merge_review m JOIN source_record s ON s.id = m.source_record_id "
        "WHERE m.status = 'open'"
    ).fetchall()

    hw = {
        r["id"]: r
        for r in conn.execute(
            "SELECT id, asset_serial, hostname, ip, vm_uuid FROM hardware"
        ).fetchall()
    }
    # 已經被佔用的 vm_uuid → 條件 6 用
    taken: dict[str, int] = {}
    for r in hw.values():
        u = identity._norm(r["vm_uuid"])
        if u:
            taken[u] = r["id"]

    resolvable: list[dict] = []
    skipped: dict[str, int] = {}
    # 同一個 uuid 可能出現在多筆待審核。要分清楚兩種完全不同的情況：
    #
    #   (a) 那幾筆指向**同一台**資產 → 是同一個決定被重複記了 N 次，不是衝突。
    #       vCenter 匯入跑過幾輪，每輪都對同一台生一筆待審核（實查 221：366 個
    #       uuid 共 1464 筆，每個平均 4 筆，資產編號、主機名、IP 全部一模一樣）。
    #       這種要一起解掉，不然清完還剩 3/4 卡在那裡，看起來像沒做事。
    #   (b) 那幾筆指向**不同台**資產 → 這才是真的說不清是哪一台，交給人。
    #
    # 第一版把 (a) 也當成衝突擋掉，1464 筆全被略過——乾跑才看出來。
    claimed: dict[str, set[int]] = {}

    def skip(why: str):
        skipped[why] = skipped.get(why, 0) + 1

    for row in rows:
        if row["source"] != VCENTER:
            skip("來源不是 vCenter（沒有夠強的識別碼）")
            continue
        ids = identity.extract(_payload(row))
        uuid = ids.get("vm_uuid")
        if not uuid:
            skip("來源沒帶 vm_uuid")
            continue
        cids = _candidate_ids(row)
        if len(cids) != 1:
            skip("候選不只一個，要先釐清是哪一台")
            continue
        cand = hw.get(cids[0])
        if cand is None:
            skip("候選資產已不存在")
            continue
        cand_uuid = identity._norm(cand["vm_uuid"])
        if cand_uuid == uuid:
            skip("候選已經是同一個 uuid（本來就該相符）")
            continue
        if cand_uuid:
            skip("兩邊 vm_uuid 不同＝衝突，一定要人看")
            continue
        agree = []
        if ids.get("ip") and identity._norm(cand["ip"]) == ids["ip"]:
            agree.append("ip")
        if ids.get("hostname") and identity._norm(cand["hostname"]) == ids["hostname"]:
            agree.append("hostname")
        if not agree:
            skip("連弱識別碼都對不上")
            continue
        if uuid in taken:
            skip("這個 vm_uuid 已經被別台資產佔用")
            continue
        claimed.setdefault(uuid, set()).add(cand["id"])
        resolvable.append({
            "review_id": row["review_id"],
            "hardware_id": cand["id"],
            "asset_serial": cand["asset_serial"],
            "asset_hostname": cand["hostname"],
            "asset_ip": cand["ip"],
            "source_hostname": ids.get("hostname"),
            "source_ip": ids.get("ip"),
            "vm_uuid": uuid,
            "matched_on": "+".join(agree),
            "rule": "vcenter_uuid_backfill",
        })

    # 最後一關：把「同一個 uuid 指向不同台資產」的整組退回人工。
    # 要等全部掃完才判得出來——只掃到一半時看起來都還只有一台。
    ambiguous = {u for u, ids_ in claimed.items() if len(ids_) > 1}
    if ambiguous:
        kept = []
        for r in resolvable:
            if r["vm_uuid"] in ambiguous:
                skip("同一個 vm_uuid 指向不同台資產，說不清是哪一台")
            else:
                kept.append(r)
        resolvable = kept

    # 「幾筆待審核」跟「幾個決定」是兩個數字，畫面上要分開講：
    # 3333 筆看起來像 3333 個判斷，實際上重複匯入讓同一個決定被記了好幾次。
    return {
        "resolvable": resolvable,
        "skipped": skipped,
        "total_open": len(rows),
        "distinct_assets": len({r["hardware_id"] for r in resolvable}),
    }


def apply(conn, username: str) -> dict:
    """把 plan() 算出來的那批真的寫進去。**寫入前一定重算一次 plan()**。

    不接受前端傳 id 清單，理由跟既有的批次合併一樣（見 api.batch_merge 的說明）：
    那等於把「要合併哪些」的決定權交給畫面，畫面算錯就把資料併到錯的機器上，
    而合併錯不會噴錯、很難發現、更難還原。

    只寫 vm_uuid 這一個欄位。業務欄位（用途、保管者、機房、盤點單位）一律不動——
    那些是人維護的，vCenter 不知道也無權覆蓋；連 os／is_vm 都不在這裡寫，
    這條規則的用途就只是「把身分證號補上去」，範圍越小越好回頭查。
    """
    from datetime import datetime

    out = plan(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for r in out["resolvable"]:
        conn.execute(
            "UPDATE hardware SET vm_uuid = ?, updated_at = ? WHERE id = ? "
            # 再確認一次還是空的：plan() 到這裡之間若有別的流程填了值，就不該覆蓋
            "AND (vm_uuid IS NULL OR trim(vm_uuid) = '')",
            (r["vm_uuid"], now, r["hardware_id"]),
        )
        conn.execute(
            "UPDATE source_record SET resolved_status = 'matched', resolved_hardware_id = ?, "
            "resolved_rule = ?, resolved_confidence = ? "
            "WHERE id = (SELECT source_record_id FROM merge_review WHERE id = ?)",
            (r["hardware_id"], f"batch:{r['rule']}", 0.95, r["review_id"]),
        )
        conn.execute(
            "UPDATE merge_review SET status = 'merged', decided_by = ?, decided_at = ? "
            "WHERE id = ? AND status = 'open'",
            (username, now, r["review_id"]),
        )
    conn.commit()
    remaining = conn.execute(
        "SELECT COUNT(*) FROM merge_review WHERE status = 'open'"
    ).fetchone()[0]
    return {
        "merged": len(out["resolvable"]),
        "distinct_assets": out["distinct_assets"],
        "remaining_open": remaining,
        "rule": "vcenter_uuid_backfill",
    }
