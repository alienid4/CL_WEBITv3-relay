"""新設備生命週期追蹤（v1.499.1，2026-10-23）。

使用者怎麼用：新機器上線後，看它卡在哪一步、卡了多久，催對的人往下走。

每台（system_stats.machine_key）依序五步，停在「第一個還沒做到的那一步」：
  1. 已登記 CIA      — 有帳內（非 DYN-/VC-/AUTO-）登記
  2. 有機櫃位置      — 機櫃＋U 看得懂（rack_layout.machines，跟機房架構圖同一份）；VM 不適用、直接過
  3. 網路掃得到      — 存在可信度有「掃描」證據，或收集帳號連上過（連得上當然掃得到）
  4. 已納管          — 收集帳號 7 天內連得上（existence 的「已納管」，全站唯一定義）
  5. 收集正常        — 4-x 納管漏斗（pipeline.summarize）這台落在「資料齊全」或「已收集（設備）」
另外三個出口（不算卡住）：退役（停用／閒置）、報廢、非納管設備（漏斗的「已豁免」）。

**不另寫漏斗**：第 4、5 步直接讀 existence.compute 與 pipeline.summarize 的結果，沒有重算判準；
pipeline 本身一行都沒改（test_device_lifecycle 釘住它的計數跟直接呼叫一致）。

卡多久：從「上一步最早做到的時間」算到今天。找得到的最早時間——
  登記＝hardware.created_at 與 source_record.collected_at 取最早；掃到＝scan_history 最早一筆活著；
  納管＝onboard_audit 最早一筆成功。機櫃位置沒有時間紀錄，卡在掃描那步就從登記時間算（標明來源）。
"""
from __future__ import annotations

import datetime as _dt

import system_report

STEPS = [
    ("registered", "已登記 CIA"),
    ("racked", "有機櫃位置"),
    ("scanned", "網路掃得到"),
    ("onboarded", "已納管"),
    ("collecting", "收集正常"),
]
STEP_KEYS = [s[0] for s in STEPS]
STEP_LABEL = dict(STEPS)
DONE = "done"
EXITS = [("retired", "退役（停用／閒置）"), ("scrapped", "報廢"), ("exempt", "非納管設備（豁免）")]
STAGE_LABEL = {**{k: f"卡在：{v}" for k, v in STEPS}, DONE: "五步都完成", **dict(EXITS)}
STAGE_ORDER = STEP_KEYS + [DONE] + [e[0] for e in EXITS]
SCRAP = {"報廢"}
NO_UNIT = "（使用單位未填）"


def _min(*vals):
    v = [x for x in vals if x]
    return min(v) if v else None


def _ts(v):
    return str(v)[:19] if v else None


def _days(since: str | None, now: _dt.datetime) -> int | None:
    if not since:
        return None
    try:
        return (now - _dt.datetime.strptime(since[:19], "%Y-%m-%d %H:%M:%S")).days
    except ValueError:
        try:
            return (now - _dt.datetime.strptime(since[:10], "%Y-%m-%d")).days
        except ValueError:
            return None


def compute(conn, now: _dt.datetime | None = None) -> dict:
    import existence
    import manage_state
    import onboard_exempt
    import pipeline
    import rack_layout

    now = now or _dt.datetime.now()
    ex = existence.compute(conn, now)["machines"]
    rack = {m["key"]: m for m in rack_layout.machines(conn)}
    pipe = pipeline.summarize(conn)
    stage_of_serial: dict[str, set] = {}
    for it in pipe["items"]:
        for s in [it.get("asset_serial")] + list(it.get("dup_serials") or []):
            if s:
                stage_of_serial.setdefault(s, set()).add(it["stage"])
    exempt = onboard_exempt.active_serials(conn)

    hw = {r["asset_serial"]: r for r in conn.execute(
        "SELECT id, asset_serial, ip, physical_location, usage_unit, api_id, asset_status, is_vm, device_model, "
        "created_at FROM hardware")}
    first_src: dict[int, str] = {}
    for r in conn.execute("SELECT resolved_hardware_id, MIN(collected_at) FROM source_record "
                          "WHERE resolved_hardware_id IS NOT NULL GROUP BY resolved_hardware_id"):
        first_src[r[0]] = _ts(r[1])
    scan_first: dict[str, str] = {}
    for r in conn.execute("SELECT ip, MIN(scan_time) FROM scan_history WHERE scan_ok = 1 GROUP BY ip"):
        if r[0]:
            scan_first[r[0].strip()] = _ts(r[1])
    onboard_first: dict[str, str] = {}
    for r in conn.execute("SELECT target_ip, MIN(created_at) FROM onboard_audit WHERE ok = 1 "
                          "AND trigger <> 'revoke' GROUP BY target_ip"):
        if r[0]:
            onboard_first[r[0].strip()] = _ts(r[1])
    names = {}
    try:
        names = {(r[0] or "").strip(): r[1] for r in conn.execute("SELECT api_id, name FROM business_system")}
    except Exception:  # noqa: BLE001 - 系統名稱只是顯示用，查不到就只顯示 APID
        pass

    rows = []
    for m in ex:
        hrs = [hw[s] for s in m["serials"] if s in hw]
        if not hrs:          # 掃到但完全沒登記的（existence 的 extra）不在這頁：那是 2-1「納入管理」的事
            continue
        inb = [r for r in hrs if not system_report.is_off_book(r["asset_serial"])]
        ips = {(r["ip"] or "").strip() for r in hrs if (r["ip"] or "").strip()}
        statuses = {(r["asset_status"] or "").strip() for r in hrs}
        rk = rack.get(m["key"])
        is_vm = bool(rk and rk["kind"] in ("vm",)) or any(
            manage_state.is_vm_value(r["is_vm"], r["device_model"]) for r in hrs)
        pstages = set().union(*[stage_of_serial.get(s, set()) for s in m["serials"]]) if m["serials"] else set()
        reg_at = _min(*[_ts(r["created_at"]) for r in inb], *[first_src.get(r["id"]) for r in inb])
        seen_at = _min(*[scan_first.get(ip) for ip in ips])
        onb_at = _min(*[onboard_first.get(ip) for ip in ips])
        done = {
            "registered": bool(inb),
            "racked": is_vm or bool(rk and ((rk["kind"] == "rack" and rk["u_start"]) or rk["kind"] == "removed")),
            "scanned": ("scan" in m["evidence"]) or m["collect"] is not None,
            "onboarded": bool(m["onboarded"]),
            "collecting": bool(pstages & {"complete", "collected"}),
        }
        if m["retired"]:
            stage = "scrapped" if statuses & SCRAP else "retired"
        elif any(s in exempt for s in m["serials"]) or "exempt" in pstages:
            stage = "exempt"
        else:
            stage = next((k for k in STEP_KEYS if not done[k]), DONE)
        since, since_src = None, None
        if stage == "registered":
            since = _min(*[_ts(r["created_at"]) for r in hrs], *[first_src.get(r["id"]) for r in hrs])
            since_src = "第一次出現在資產庫"
        elif stage in ("racked", "scanned"):
            since, since_src = reg_at, "登記"
        elif stage == "onboarded":
            since, since_src = (seen_at, "第一次掃到") if seen_at else (reg_at, "登記")
        elif stage == "collecting":
            since, since_src = (onb_at, "第一次納管成功") if onb_at else (seen_at or reg_at, "第一次掃到" if seen_at else "登記")
        loc = next((r["physical_location"] for r in hrs if (r["physical_location"] or "").strip()), None)
        apis = sorted({(r["api_id"] or "").strip() for r in hrs if (r["api_id"] or "").strip()})
        rows.append({
            "key": m["key"], "serial": (inb[0] if inb else hrs[0])["asset_serial"],
            "hostname": m["hostname"], "ip": m["ip"], "is_vm": is_vm,
            "room": rk["room"] if rk else _room(loc), "rack": rk["rack_raw"] if rk else None,
            "api_ids": apis, "systems": "、".join(f"{a} {names.get(a) or ''}".strip() for a in apis) or None,
            "unit": next((r["usage_unit"].strip() for r in inb if (r["usage_unit"] or "").strip()), NO_UNIT),
            "stage": stage, "stage_label": STAGE_LABEL[stage],
            "stage_index": STAGE_ORDER.index(stage),
            "steps_done": sum(1 for k in STEP_KEYS if done[k]), "done": done,
            "pipeline_stages": sorted(pstages),
            "registered_at": reg_at, "first_scan_at": seen_at, "first_onboard_at": onb_at,
            "stuck_since": since, "stuck_since_src": since_src, "stuck_days": _days(since, now),
        })
    rows.sort(key=lambda r: (r["stage_index"], -(r["stuck_days"] or -1), r["serial"]))
    counts = {k: 0 for k in STAGE_ORDER}
    for r in rows:
        counts[r["stage"]] += 1
    active = [r for r in rows if r["stage"] in STEP_KEYS or r["stage"] == DONE]
    reached = {k: sum(1 for r in active if STEP_KEYS.index(k) < (STEP_KEYS.index(r["stage"])
                                                                  if r["stage"] in STEP_KEYS else len(STEP_KEYS)))
               for k in STEP_KEYS}
    return {"generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "steps": [{"key": k, "label": lbl} for k, lbl in STEPS],
            "exits": [{"key": k, "label": lbl} for k, lbl in EXITS],
            "stage_labels": STAGE_LABEL, "stage_order": STAGE_ORDER,
            "total": len(rows), "active": len(active), "counts": counts, "reached": reached,
            "pipeline_counts": pipe["counts"], "rows": rows}


def _room(loc):
    import blast_radius
    return blast_radius.map_room(loc)
