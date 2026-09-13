"""上線前檢查表 ＋ 基線回檢（drift）。

三件事串在一起（2026-08-15 與使用者定案的模型）：

  申請單 → 產生 draft 資產 → **上線前檢查表** → 通過才轉「使用中」
                                    ↓ 通過那一刻的 auto 項結果 = 基線
                                **每天回檢**，跟基線不一樣就是 drift

為什麼要有基線這層：使用者原話是「哪天防堵被拿掉了，我們也可以知道防堵本來是刻意的，
現在已經失效了」。單純每天掃現況只能說「這台在聽 23」，不能說「這台的 Telnet 是上線時
刻意關掉的、現在被打開了、當初簽核的人是誰」——差別在有沒有一份「應然」可以對照。

刻意的保守設計：**收不到資料一律 unknown，不當成通過、也不當成 drift**。
沒有服務採集資料的主機（沒納管、當天採集失敗）如果被判成「Telnet 已停用」，
那是最糟的結果——畫面顯示綠燈，實際上根本沒人看過那台機器。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

CHECKLIST_PATH = Path(__file__).parent / "golive_checklist.json"

# 判定結果。pass/na 才算「這項處理完了」，fail/unknown 都擋著不讓上線。
PASSED_VERDICTS = ("pass", "na")


def load_checklist() -> list[dict]:
    """讀檢查項目定義。設定檔壞掉直接讓它噴——這不是可以默默降級的東西，
    檢查表少了一半項目卻照樣讓人按「通過」，比整頁壞掉危險得多。"""
    return json.loads(CHECKLIST_PATH.read_text(encoding="utf-8"))["items"]


def _os_scope_of(os_text: str | None) -> str:
    """由 hardware.os 判斷這台算 windows 還是 linux。認不出來回 'unknown'——
    這時候所有項目都顯示（寧可多問幾項，也不要漏掉該做的設定）。"""
    s = (os_text or "").lower()
    if "windows" in s or "microsoft" in s:
        return "windows"
    if any(k in s for k in ("linux", "rhel", "red hat", "centos", "rocky", "ubuntu",
                            "debian", "suse", "oracle linux", "aix")):
        return "linux"
    return "unknown"


def items_for_asset(conn: sqlite3.Connection, asset_serial: str) -> list[dict]:
    """這台資產該做哪些檢查項（依 OS 過濾掉不適用的）。"""
    row = conn.execute(
        "SELECT os FROM hardware WHERE asset_serial = ?", (asset_serial,)
    ).fetchone()
    scope = _os_scope_of(row["os"] if row else None)
    items = load_checklist()
    if scope == "unknown":
        return items
    return [i for i in items if i.get("os_scope", "both") in ("both", scope)]


# ===== auto 項判定：只用系統既有的事實（host_service 監聽埠 / hardware 欄位）=====

def _has_service_data(conn: sqlite3.Connection, ip: str) -> bool:
    """這台有沒有服務採集資料。沒有的話所有埠相關的判定都只能是 unknown，
    不能把「沒去收」講成「沒有在聽」。"""
    r = conn.execute(
        "SELECT 1 FROM host_service WHERE ip = ? LIMIT 1", (ip,)
    ).fetchone()
    return r is not None


def _port_listening(conn: sqlite3.Connection, ip: str, port: int, proto: str) -> bool:
    """gone_at IS NULL＝目前還在聽。消失的服務不刪除只標時間（見 schema 註解）。"""
    r = conn.execute(
        "SELECT 1 FROM host_service "
        "WHERE ip = ? AND port = ? AND proto = ? AND gone_at IS NULL LIMIT 1",
        (ip, port, proto),
    ).fetchone()
    return r is not None


def evaluate_auto_items(conn: sqlite3.Connection, asset_serial: str) -> dict[str, dict]:
    """回傳 {item_key: {"verdict": ..., "state": ..., "evidence": ...}}。

    state 是拿來跟基線比對的正規化字串（present/absent/欄位值），
    evidence 是給人看的說明；兩者分開是因為 evidence 會寫得比較白話，
    拿它做字串比對遲早會因為文案調整就誤報 drift。
    """
    hw = conn.execute(
        "SELECT * FROM hardware WHERE asset_serial = ?", (asset_serial,)
    ).fetchone()
    if hw is None:
        return {}
    ip = (hw["ip"] or "").strip()
    has_svc = bool(ip) and _has_service_data(conn, ip)

    out: dict[str, dict] = {}
    for item in items_for_asset(conn, asset_serial):
        if item.get("check_type") != "auto":
            continue
        fact = item.get("fact") or {}
        kind = fact.get("type")

        if kind == "field_equals":
            value = (hw[fact["field"]] if fact["field"] in hw.keys() else None) or ""
            value = str(value).strip()
            out[item["key"]] = {
                "verdict": "pass" if value else "unknown",
                "state": value,
                "evidence": value or "（欄位是空的，測不到）",
            }
            continue

        if kind in ("port_absent", "port_present"):
            port = int(fact["port"])
            proto = fact.get("proto", "tcp")
            if not has_svc:
                out[item["key"]] = {
                    "verdict": "unknown",
                    "state": "unknown",
                    "evidence": "這台沒有服務採集資料，無法判定" if ip else "資產沒有 IP，無法判定",
                }
                continue
            listening = _port_listening(conn, ip, port, proto)
            state = "present" if listening else "absent"
            want_present = kind == "port_present"
            out[item["key"]] = {
                "verdict": "pass" if listening == want_present else "fail",
                "state": state,
                "evidence": f"{proto}/{port} " + ("監聽中" if listening else "沒有監聽"),
            }
            continue

    return out


# ===== 檢查表本體 =====

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_check(conn: sqlite3.Connection, asset_serial: str) -> sqlite3.Row:
    """取得（或建立）這台資產的上線檢查表。"""
    row = conn.execute(
        "SELECT * FROM golive_check WHERE asset_serial = ?", (asset_serial,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO golive_check (asset_serial, status, started_at) VALUES (?, 'open', ?)",
            (asset_serial, _now()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM golive_check WHERE asset_serial = ?", (asset_serial,)
        ).fetchone()
    return row


def refresh_auto_results(conn: sqlite3.Connection, asset_serial: str) -> None:
    """把 auto 項的最新判定寫進結果表。已通過的檢查表不再覆寫——
    通過那一刻的結果就是基線，之後的變化要走 drift，不能回頭改基線本身。"""
    check = ensure_check(conn, asset_serial)
    if check["status"] == "passed":
        return
    for key, r in evaluate_auto_items(conn, asset_serial).items():
        conn.execute(
            "INSERT INTO golive_check_result "
            "(check_id, item_key, verdict, evidence, checked_by, checked_at) "
            "VALUES (?, ?, ?, ?, 'auto', ?) "
            "ON CONFLICT(check_id, item_key) DO UPDATE SET "
            "verdict = excluded.verdict, evidence = excluded.evidence, "
            "checked_by = 'auto', checked_at = excluded.checked_at",
            (check["id"], key, r["verdict"], r["evidence"], _now()),
        )
    conn.commit()


def get_check_detail(conn: sqlite3.Connection, asset_serial: str) -> dict:
    """一份檢查表的完整內容（項目定義 + 目前判定），給畫面用。"""
    check = ensure_check(conn, asset_serial)
    results = {
        r["item_key"]: r
        for r in conn.execute(
            "SELECT * FROM golive_check_result WHERE check_id = ?", (check["id"],)
        ).fetchall()
    }
    items = []
    for item in items_for_asset(conn, asset_serial):
        r = results.get(item["key"])
        items.append({
            **item,
            "verdict": r["verdict"] if r else None,
            "evidence": r["evidence"] if r else None,
            "checked_by": r["checked_by"] if r else None,
            "checked_at": r["checked_at"] if r else None,
        })
    done = [i for i in items if i["verdict"] in PASSED_VERDICTS]
    return {
        "asset_serial": asset_serial,
        "status": check["status"],
        "started_at": check["started_at"],
        "passed_at": check["passed_at"],
        "passed_by": check["passed_by"],
        "items": items,
        "total": len(items),
        "done": len(done),
        "blocking": [
            {"key": i["key"], "label": i["label"], "verdict": i["verdict"]}
            for i in items if i["verdict"] not in PASSED_VERDICTS
        ],
    }


def set_item_verdict(
    conn: sqlite3.Connection, asset_serial: str, item_key: str, verdict: str, who: str
) -> None:
    """人工勾一項。auto 項不給人工覆寫——那等於讓人手動宣告一件機器說了不算的事，
    基線就失去意義了（要放行請用『不需(na)』以外的正當理由，或修好機器再回檢）。"""
    check = ensure_check(conn, asset_serial)
    if check["status"] == "passed":
        raise ValueError("這份檢查表已通過，不能再改；設定有變動請看基線失效清單")
    item = next((i for i in items_for_asset(conn, asset_serial) if i["key"] == item_key), None)
    if item is None:
        raise ValueError(f"沒有這個檢查項目：{item_key}")
    if item.get("check_type") == "auto" and verdict != "na":
        raise ValueError("自動判定的項目不能人工勾選，只能標「不需」")
    if verdict not in ("pass", "na", "fail"):
        raise ValueError(f"不支援的判定值：{verdict}")
    conn.execute(
        "INSERT INTO golive_check_result "
        "(check_id, item_key, verdict, checked_by, checked_at) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(check_id, item_key) DO UPDATE SET "
        "verdict = excluded.verdict, checked_by = excluded.checked_by, "
        "checked_at = excluded.checked_at",
        (check["id"], item_key, verdict, who, _now()),
    )
    conn.commit()


def pass_check(conn: sqlite3.Connection, asset_serial: str, who: str) -> dict:
    """全部項目處理完才准通過，並把資產轉「使用中」。

    通過的同時把 auto 項當下的狀態存成基線（存在 golive_check_result.evidence 旁邊的
    state，用 baseline_drift 的 baseline 欄位持有），之後每天回檢就跟它比。
    """
    refresh_auto_results(conn, asset_serial)
    detail = get_check_detail(conn, asset_serial)
    if detail["status"] == "passed":
        return detail
    if detail["blocking"]:
        raise ValueError(
            "還有 %d 項沒處理完：%s"
            % (len(detail["blocking"]), "、".join(b["label"] for b in detail["blocking"][:3]))
        )

    now = _now()
    conn.execute(
        "UPDATE golive_check SET status = 'passed', passed_at = ?, passed_by = ? "
        "WHERE asset_serial = ?",
        (now, who, asset_serial),
    )
    # 基線落地：只記 auto 項（人工項沒有機器可以回檢，記了也沒人比對）
    for key, r in evaluate_auto_items(conn, asset_serial).items():
        if r["state"] in ("", "unknown"):
            continue
        conn.execute(
            "INSERT INTO baseline_drift "
            "(asset_serial, item_key, baseline, current, status, first_detected_at, "
            " last_detected_at, resolved_at) "
            "VALUES (?, ?, ?, ?, 'fixed', NULL, NULL, ?) "
            "ON CONFLICT(asset_serial, item_key) DO UPDATE SET "
            "baseline = excluded.baseline, current = excluded.current, "
            "status = 'fixed', resolved_at = excluded.resolved_at",
            (asset_serial, key, r["state"], r["state"], now),
        )
    conn.execute(
        "UPDATE hardware SET asset_status = '使用中', updated_at = ? WHERE asset_serial = ?",
        (now, asset_serial),
    )
    conn.commit()
    return get_check_detail(conn, asset_serial)


# ===== 每日回檢 =====

def run_drift_check(conn: sqlite3.Connection) -> dict:
    """對所有已通過上線檢查的資產重跑一次 auto 項，跟基線比對。

    回檢時機掛在每日掃描之後（服務採集剛跑完，資料最新），見 scan_service。

    三種結果：
      · 跟基線一樣          → 既有的 drift 標成 fixed（自己恢復了也要記錄）
      · 跟基線不一樣        → 開一筆 drift（已存在就更新 last_detected_at，不重複開）
      · 測不到（unknown）   → 什麼都不做，維持原狀（沒收到資料不是異常）
    """
    import manage_state as ms

    now = _now()
    # 退役資產（停用／報廢／閒置）排除：機器關機後所有 port 都不見了，每一項基線都會
    # 不符，每天噴一整台份的假告警。這跟這支開頭寫的原則是同一件事——假告警會讓整張表
    # 沒人看。（2026-08-15 自我檢查抓到，原本沒擋。）
    ph = ",".join("?" for _ in ms.RETIRED_STATUS)
    serials = [
        r["asset_serial"]
        for r in conn.execute(
            f"SELECT g.asset_serial FROM golive_check g "
            f"JOIN hardware h ON h.asset_serial = g.asset_serial "
            f"WHERE g.status = 'passed' "
            f"AND COALESCE(h.asset_status, '') NOT IN ({ph})",
            tuple(ms.RETIRED_STATUS),
        ).fetchall()
    ]
    opened = recovered = 0
    for serial in serials:
        baselines = {
            r["item_key"]: r
            for r in conn.execute(
                "SELECT * FROM baseline_drift WHERE asset_serial = ?", (serial,)
            ).fetchall()
        }
        for key, r in evaluate_auto_items(conn, serial).items():
            base = baselines.get(key)
            if base is None or not base["baseline"]:
                continue
            state = r["state"]
            if state in ("", "unknown"):
                continue
            if state == base["baseline"]:
                if base["status"] != "fixed":
                    conn.execute(
                        "UPDATE baseline_drift SET status = 'fixed', current = ?, "
                        "resolved_at = ? WHERE id = ?",
                        (state, now, base["id"]),
                    )
                    recovered += 1
                continue
            # 已經開著的 drift 只更新時間，不重複開——每天亮同一條紅燈會讓人直接忽略整張表
            if base["status"] in ("open", "ack"):
                conn.execute(
                    "UPDATE baseline_drift SET current = ?, last_detected_at = ? WHERE id = ?",
                    (state, now, base["id"]),
                )
            else:
                conn.execute(
                    "UPDATE baseline_drift SET status = 'open', current = ?, "
                    "first_detected_at = ?, last_detected_at = ?, resolved_at = NULL, "
                    "decided_by = NULL, decided_at = NULL WHERE id = ?",
                    (state, now, now, base["id"]),
                )
                opened += 1
    conn.commit()
    return {"checked_assets": len(serials), "opened": opened, "recovered": recovered}


_STATE_LABEL = {"present": "服務運作中", "absent": "服務已停用"}


def describe_state(item_key: str, state: str | None) -> str:
    """把正規化狀態翻成人看得懂的字（畫面與匯出共用，避免兩邊翻譯不一致）。"""
    if not state:
        return "—"
    return _STATE_LABEL.get(state, state)


def list_drift(conn: sqlite3.Connection, status: str | None = "open") -> list[dict]:
    """基線失效清單。預設只看還沒處理的。"""
    labels = {i["key"]: i["label"] for i in load_checklist()}
    sql = (
        "SELECT d.*, h.hostname, h.ip, h.asset_status, g.passed_at, g.passed_by "
        "FROM baseline_drift d "
        "LEFT JOIN hardware h ON h.asset_serial = d.asset_serial "
        "LEFT JOIN golive_check g ON g.asset_serial = d.asset_serial "
        "WHERE d.baseline IS NOT NULL"
    )
    params: list = []
    if status:
        sql += " AND d.status = ?"
        params.append(status)
    else:
        # 沒指定狀態時不回傳 fixed：那些是「符合基線」的正常狀態，有幾千筆，不是清單要看的東西
        sql += " AND d.status != 'fixed'"
    sql += " ORDER BY d.last_detected_at DESC, d.asset_serial"
    rows = conn.execute(sql, params).fetchall()
    return [
        {
            **dict(r),
            "label": labels.get(r["item_key"], r["item_key"]),
            "baseline_text": describe_state(r["item_key"], r["baseline"]),
            "current_text": describe_state(r["item_key"], r["current"]),
        }
        for r in rows
    ]
