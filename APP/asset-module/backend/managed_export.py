"""2-1「已納管主機」匯出 Excel（2026-10-20 使用者：「把納管主機 整理EXCEL匯出」）。

## 列哪些、怎麼篩——跟畫面同一份

- 母體＝ `/api/manage-state?state=已納管` ＋ `state=已納管但失聯`（畫面那張表就是這兩份接起來），
  項次＝這個接起來的順序（1、2、3…），跟畫面「#」欄同一個號碼，篩選後號碼不變。
- 篩選條件照畫面：OS（`os_type`，空的算「未填」）、標籤（點表格裡的標籤）、關鍵字（IP／主機名稱／序號，
  不分大小寫、子字串）。畫面是前端篩、這裡是後端篩——**規則一字不差**，`test_managed_export.py` 釘住。
- 匯出的是「篩完的全部列」，不是畫面上剛好畫出來的那幾列。

不碰任何憑證：「納管帳號」只列帳號名稱；Windows 列「WinRM 憑證」，不列帳號密碼。
"""
from __future__ import annotations

HEADERS = ["項次", "IP", "主機名稱", "OS", "資產序號", "標籤", "最後確認收得到", "上次失敗",
           "狀態", "機房", "環境別", "作業系統（資產登記）", "納管帳號"]


def _job_headers() -> list[str]:
    import post_onboard

    return [f"{post_onboard.JOB_LABEL[j]}（最近結果）" for j in post_onboard.JOBS] + ["六樣收集時間"]


def headers() -> list[str]:
    return HEADERS + _job_headers()


def base_rows(conn) -> list[dict]:
    """畫面那張表的母體＋固定項次。"""
    import manage_state

    rep = manage_state.summarize(conn)["rep_items"]
    items = list(rep.get(manage_state.ONBOARDED, [])) + list(rep.get(manage_state.ONBOARDED_STALE, []))
    return [{**m, "no": i + 1} for i, m in enumerate(items)]


def filter_rows(rows: list[dict], tags_by_serial: dict, *, kw: str = "", os: str = "",
                tag: str = "") -> list[dict]:
    """跟 adopt.vue 的 managedShown 同一套規則。"""
    k = (kw or "").strip().lower()
    out = []
    for m in rows:
        if os and (m.get("os_type") or "未填") != os:
            continue
        tags = tags_by_serial.get(m.get("asset_serial") or "", []) if m.get("asset_serial") else []
        if tag and tag not in tags:
            continue
        if k and not any(k in (m.get(f) or "").lower() for f in ("ip", "hostname", "asset_serial")):
            continue
        out.append(m)
    return out


def _account(conn, serial: str | None, os_type: str | None) -> str:
    if (os_type or "") == "Windows":
        return "WinRM 憑證"
    if not serial:
        return ""
    try:
        import collect_account_migration as cam

        return cam.account_for_host(conn, serial, "aix" if os_type == "AIX" else "linux") or ""
    except Exception:  # noqa: BLE001 - 查不到就留空（不猜一個帳號名）
        return ""


def export_rows(conn, *, kw: str = "", os: str = "", tag: str = "") -> list[list]:
    import asset_tags
    import collect_log
    import onboard_failures
    import post_onboard

    rows = base_rows(conn)
    serials = [m["asset_serial"] for m in rows if m.get("asset_serial")]
    raw_tags = asset_tags.tags_by_serial(conn, serials) if serials else {}
    tags_by = {s: [t["tag"] if isinstance(t, dict) else str(t) for t in v] for s, v in raw_tags.items()}
    rows = filter_rows(rows, tags_by, kw=kw, os=os, tag=tag)
    fails = onboard_failures.last_attempts(conn, [m["ip"] for m in rows if m.get("ip")]) if rows else {}
    hw = {}
    sel = [m["asset_serial"] for m in rows if m.get("asset_serial")]
    for i in range(0, len(sel), 500):
        chunk = sel[i:i + 500]
        for r in conn.execute(
                "SELECT asset_serial, physical_location, environment, os FROM hardware "
                f"WHERE asset_serial IN ({','.join('?' * len(chunk))})", chunk):
            hw[r["asset_serial"]] = r

    out = []
    for m in rows:
        sn = m.get("asset_serial")
        h = hw.get(sn)
        f = (fails.get(m.get("ip")) or {}).get("fail") if m.get("ip") else None
        last = collect_log.latest_for(conn, "post_onboard", [t for t in (sn, m.get("ip")) if t])
        res = {r.get("job"): r for r in ((last or {}).get("detail") or {}).get("results") or []}
        jobs = []
        for j in post_onboard.JOBS:
            r = res.get(j)
            if r is None:
                jobs.append("尚未收集" if last is None else "")
            elif r.get("ok") is None:
                jobs.append(f"略過：{r.get('message') or ''}")
            elif r.get("ok"):
                jobs.append("成功")
            else:
                jobs.append(f"失敗：{r.get('message') or ''}")
        out.append([
            m["no"], m.get("ip") or "", m.get("hostname") or "", m.get("os_type") or "未填", sn or "",
            "、".join(tags_by.get(sn, []) if sn else []), m.get("collect_checked_at") or "",
            (f"{f.get('at')}　{f.get('short') or ''}" if f else ""), m.get("state") or "",
            (h["physical_location"] if h else None) or "", (h["environment"] if h else None) or "",
            (h["os"] if h else None) or "", _account(conn, sn, m.get("os_type")),
            *jobs, (last or {}).get("at") or "",
        ])
    return out


def workbook_bytes(conn, *, kw: str = "", os: str = "", tag: str = "") -> bytes:
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "已納管主機"
    ws.append(headers())
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in export_rows(conn, kw=kw, os=os, tag=tag):
        ws.append(r)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for col, w in zip("ABCDEFGHIJKLM", (6, 16, 22, 10, 18, 18, 20, 30, 12, 14, 10, 28, 14)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
