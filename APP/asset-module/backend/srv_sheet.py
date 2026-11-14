"""伺服器資料彙整表（參考來源；2.060.20，S10）。

主機組流程「先寫這張表 → 建主機 → 上線 → 盤點 → CIA」：這張表是最上游、有人持續更新，CIA 是最下游。
所以這張表拿來當**證據**，不改任何資產資料（不寫 hardware、不改 CIA；CIA 正本只由 CIA 匯入寫入）：
  1. 表有、CIA 無 ＝ 上線後漏登 CIA → 9.4 補登最強的證據。
  2. 逐欄比對「表／CIA／系統實掃」（主機名、作業系統、環境別、負責人、用途、機房），對不上就標出來。
  3. 同一列自己矛盾（環境寫正式、用途或資產名稱卻寫測試／POC…）也標。
  4. 「已關機待處理」分頁：把已關機的先剔出來，不當成漏登。
差異不逐台問人：一律做成清單（可篩選、排序、下鑽、匯出 Excel 批次確認）。

只讀兩張分頁：名稱含「伺服器」與「主」的那張（第 1 列英文欄名、第 2 列中文表頭），名稱含「已關機待處理」的那張。
欄位用表頭文字找（不靠欄位位置）；找不到必要欄（IP）就整檔拒收、講清楚少了什麼。
每次匯入整份取代上一份（它是快照，不是異動）；匯入紀錄留著（誰、何時、哪個檔、sha256、幾列）。
同一台的對應一律用全站同一把尺（oncall._groups → system_stats.machine_key 分組），IP 對到哪一組就是哪一台。
"""
from __future__ import annotations

import hashlib
import io
import re
import sqlite3
from datetime import datetime

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAIN_HINTS = ("伺服器", "主")
OFF_HINT = "已關機待處理"

# 中文表頭（去掉空白與換行後比對）→ 欄位
MAIN_COLS = {
    "IP": "ip", "資產用途": "purpose", "系統等級": "sys_level", "廠牌": "vendor", "實體/VM": "kind",
    "環境別": "env", "Infra組別": "infra_group", "Infra負責人": "infra_owner", "AP負責單位": "ap_unit",
    "AP負責人": "ap_owner", "作業系統": "os", "作業系統版本": "os_ver", "HostName": "host", "主機序號": "serial",
    "放置地點": "location", "機櫃位置": "rack", "資訊資產名稱": "asset_name",
}
MAIN_PREFIX = {"ID": "sheet_id", "標記時間": "marked_at"}
OFF_COLS = {"IP": "ip", "處理人員": "handler", "關機日期": "off_date", "主機序號": "serial", "原用途": "purpose"}
FIELDS = list(MAIN_COLS.values()) + list(MAIN_PREFIX.values())

KINDS = {"no_cia": "表有、CIA 無", "diff": "欄位不一致", "self": "同列矛盾", "off": "已關機", "no_ip": "IP 空白或格式錯", "all": "全部"}
FIELD_LABEL = {"host": "主機名", "os": "作業系統", "env": "環境別", "owner": "負責人", "purpose": "用途", "room": "機房"}

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS srv_sheet_batch (id INTEGER PRIMARY KEY AUTOINCREMENT, imported_at TEXT NOT NULL, imported_by TEXT, "
    "file_name TEXT, sha256 TEXT, main_sheet TEXT, rows INTEGER, off_rows INTEGER, data_date TEXT)",
    "CREATE TABLE IF NOT EXISTS srv_sheet_row (id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER NOT NULL, row_no INTEGER, ip TEXT, ips TEXT, "
    + ", ".join(f"{f} TEXT" for f in FIELDS if f != "ip") + ")",
    "CREATE TABLE IF NOT EXISTS srv_sheet_off (id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER NOT NULL, row_no INTEGER, ip TEXT, "
    "handler TEXT, off_date TEXT, kind TEXT, serial TEXT, purpose TEXT)",
    "CREATE INDEX IF NOT EXISTS idx_srv_sheet_row_ip ON srv_sheet_row(ip)",
)

_IP = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


def ensure(conn) -> None:
    for s in SCHEMA:
        conn.execute(s)


def _has(conn, t: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def _norm_head(v) -> str:
    return re.sub(r"\s+", "", str(v or ""))


_NULLS = {"#N/A", "N/A", "NA", "-", "?", "？", "待確認", "無", "#VALUE!", "#REF!"}


def _txt(v) -> str:
    """儲存格 → 文字。#N/A、N/A、? 這類佔位當空白（不是真的值，不拿來比對）。"""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    t = re.sub(r"\s+", " ", str(v)).strip()
    return "" if t.upper() in _NULLS else t


def _date_txt(v) -> str:
    """標記時間：日期格式或 Excel 序號（45587.59＝2024-10-22 14:18）都轉成 YYYY-MM-DD HH:MM:SS。"""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 20000 < float(v) < 80000:
        from datetime import timedelta
        return (datetime(1899, 12, 30) + timedelta(days=float(v))).strftime("%Y-%m-%d %H:%M:%S")
    return _txt(v)


def ips_of(v) -> list[str]:
    """一格可能寫多個 IP（逗號、斜線、換行）；只收合法 IPv4，前導零去掉（010.099.001.011 → 10.99.1.11）。"""
    out = []
    for m in _IP.findall(str(v or "")):
        parts = m.split(".")
        if all(p.isdigit() and int(p) <= 255 for p in parts):
            ip = ".".join(str(int(p)) for p in parts)
            if ip not in out and ip != "0.0.0.0":
                out.append(ip)
    return out


class Rejected(ValueError):
    pass


def parse(data: bytes) -> dict:
    """讀檔，回 {main_sheet, rows: [dict], off: [dict], data_date}。不寫庫。"""
    if data[:8] == bytes.fromhex("D0CF11E0A1B11AE1"):
        raise Rejected("這個檔有密碼（或是舊版 .xls）：請在 Excel 另存一份不加密的 .xlsx 再上傳（系統不收、也不存密碼）")
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - 壞檔、不是 xlsx：講原因拒收
        raise Rejected(f"讀不出這個 Excel：{type(exc).__name__}") from exc
    try:
        main = next((n for n in wb.sheetnames if all(h in n for h in MAIN_HINTS)), None)
        if not main:
            raise Rejected("找不到「伺服器 (主)」分頁（分頁名稱要含「伺服器」和「主」）")
        rows, data_date = _read_main(wb[main])
        off_name = next((n for n in wb.sheetnames if OFF_HINT in n), None)
        off = _read_off(wb[off_name]) if off_name else []
    finally:
        wb.close()
    return {"main_sheet": main, "rows": rows, "off": off, "off_sheet": off_name, "data_date": data_date}


def _header(ws, cols: dict, prefix: dict | None, scan: int = 5) -> tuple[int, dict[str, int]]:
    """在前幾列找表頭（含 IP 那一列裡對到最多欄的）。回 (表頭列號, {欄位: 欄 index})。"""
    best = (0, {})
    for i, row in enumerate(ws.iter_rows(min_row=1, max_row=scan, values_only=True), start=1):
        heads = [_norm_head(v) for v in row]
        m: dict[str, int] = {}
        for j, h in enumerate(heads):
            if h in cols and cols[h] not in m:
                m[cols[h]] = j
            for p, f in (prefix or {}).items():
                if h.startswith(p) and f not in m:
                    m[f] = j
        if "ip" in m and len(m) > len(best[1]):
            best = (i, m)
    return best


def _read_main(ws) -> tuple[list[dict], str | None]:
    hr, m = _header(ws, MAIN_COLS, MAIN_PREFIX)
    if not hr:
        raise Rejected("「伺服器 (主)」前 5 列找不到「IP」表頭")
    need = [k for k, f in MAIN_COLS.items() if f in ("ip", "host", "env", "purpose") and f not in m]
    if need:
        raise Rejected(f"「伺服器 (主)」少了這些欄：{'、'.join(need)}")
    out, newest = [], None
    for i, row in enumerate(ws.iter_rows(min_row=hr + 1, values_only=True), start=hr + 1):
        rec = {f: (_date_txt if f == "marked_at" else _txt)(row[j]) if j < len(row) else "" for f, j in m.items()}
        if not any(rec.values()):
            continue
        ips = ips_of(rec.get("ip"))
        rec["ips"] = ",".join(ips)
        rec["ip_raw"] = rec.get("ip", "")
        rec["ip"] = ips[0] if ips else ""
        rec["row_no"] = i
        if rec.get("marked_at") and (newest is None or rec["marked_at"] > newest):
            newest = rec["marked_at"]
        out.append(rec)
    return out, (newest[:10] if newest else None)


def _read_off(ws) -> list[dict]:
    hr, m = _header(ws, OFF_COLS, None)
    if not hr:
        return []
    kind_j = None
    for row in ws.iter_rows(min_row=hr, max_row=hr, values_only=True):
        kind_j = next((j for j, v in enumerate(row) if "VM" in _norm_head(v).upper() and "刪除" not in str(v or "")), None)
    out = []
    for i, row in enumerate(ws.iter_rows(min_row=hr + 1, values_only=True), start=hr + 1):
        rec = {f: _txt(row[j]) if j < len(row) else "" for f, j in m.items()}
        ips = ips_of(rec.get("ip"))
        if not ips:
            continue
        rec["kind"] = _txt(row[kind_j]) if kind_j is not None and kind_j < len(row) else ""
        for ip in ips:
            out.append({**rec, "ip": ip, "row_no": i})
    return out


# ── 匯入（兩段式：預覽不寫庫、確認時比對 sha256）────────────────────────
def preview(conn, data: bytes, file_name: str | None) -> dict:
    p = parse(data)
    rows = p["rows"]
    return {"file_name": file_name, "sha256": hashlib.sha256(data).hexdigest(), "main_sheet": p["main_sheet"],
            "off_sheet": p["off_sheet"], "rows": len(rows), "with_ip": sum(1 for r in rows if r["ip"]),
            "no_ip": sum(1 for r in rows if not r["ip"]), "off_rows": len(p["off"]), "data_date": p["data_date"],
            "current": last_batch(conn)}


def confirm(conn, data: bytes, file_name: str | None, expect_sha: str, by: str) -> dict:
    sha = hashlib.sha256(data).hexdigest()
    if sha != (expect_sha or "").strip():
        raise Rejected("檔案跟預覽時不一樣，請重新預覽")
    p = parse(data)
    ensure(conn)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if conn.in_transaction:
        conn.commit()                                   # ensure() 的 DDL 可能開了隱含交易；先收掉再開寫入交易
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("INSERT INTO srv_sheet_batch (imported_at, imported_by, file_name, sha256, main_sheet, rows, off_rows, data_date) "
                           "VALUES (?,?,?,?,?,?,?,?)", (now, by, file_name, sha, p["main_sheet"], len(p["rows"]), len(p["off"]), p["data_date"]))
        bid = cur.lastrowid
        conn.execute("DELETE FROM srv_sheet_row")           # 快照：整份取代（匯入紀錄留在 srv_sheet_batch）
        conn.execute("DELETE FROM srv_sheet_off")
        cols = ["batch_id", "row_no", "ip", "ips"] + [f for f in FIELDS if f != "ip"]
        conn.executemany(f"INSERT INTO srv_sheet_row ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                         [(bid, r["row_no"], r["ip"], r["ips"], *[r.get(f, "") for f in FIELDS if f != "ip"]) for r in p["rows"]])
        conn.executemany("INSERT INTO srv_sheet_off (batch_id, row_no, ip, handler, off_date, kind, serial, purpose) VALUES (?,?,?,?,?,?,?,?)",
                         [(bid, o["row_no"], o["ip"], o.get("handler", ""), o.get("off_date", ""), o.get("kind", ""), o.get("serial", ""),
                           o.get("purpose", "")) for o in p["off"]])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"ok": True, "batch": last_batch(conn)}


def last_batch(conn) -> dict | None:
    if not _has(conn, "srv_sheet_batch"):
        return None
    r = conn.execute("SELECT * FROM srv_sheet_batch ORDER BY id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def batches(conn, limit: int = 50) -> list[dict]:
    if not _has(conn, "srv_sheet_batch"):
        return []
    return [dict(r) for r in conn.execute("SELECT * FROM srv_sheet_batch ORDER BY id DESC LIMIT ?", (limit,))]


# ── 比對 ──────────────────────────────────────────────────────────────
_ENV = (("DR", ("DR", "備援", "災備", "異地")), ("測試", ("測試", "UAT", "SIT", "TEST", "POC", "QA", "STG", "STAGING")),
        ("開發", ("開發", "DEV")), ("正式", ("正式", "PROD", "PRD", "生產", "營運")))
_TESTY = re.compile(r"測試|POC|UAT|SIT|\bDEV\b|\bTEST\b|開發|驗證機|實驗", re.I)


def env_of(v: str | None) -> str | None:
    t = (v or "").upper()
    if not t.strip():
        return None
    if "非正式" in t:
        return "測試"
    for lab, keys in _ENV:
        if any(k in t for k in keys):
            return lab
    return None


def os_family(v: str | None) -> str | None:
    t = (v or "").lower()
    for fam, keys in (("Windows", ("windows", "win20", "win 20")), ("AIX", ("aix",)), ("ESXi", ("esxi", "vmware esx")),
                      ("Solaris", ("solaris", "sunos")), ("HP-UX", ("hp-ux", "hpux")),
                      ("Linux", ("linux", "rhel", "red hat", "redhat", "centos", "rocky", "oracle", "ubuntu", "suse", "debian", "alma"))):
        if any(k in t for k in keys):
            return fam
    return None


def _win_year(v: str | None) -> str | None:
    m = re.search(r"20(0[38]|1[2-9]|2\d)", v or "")
    return m.group(0) if m and os_family(v) == "Windows" else None


def _short_host(v: str | None) -> str:
    return (v or "").strip().lower().split(".")[0]


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"[\s\-_/、,，()（）:：.]+", "", (s or "").lower())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _names(v: str | None) -> set[str]:
    return {x for x in re.split(r"[\s、,，/;；()（）]+", v or "") if len(x) >= 2}


def _ip_map(groups: dict) -> dict[str, str]:
    """IP → machine_key（同一台多筆登記、一格多個 IP 都算）。現役優先於退役。"""
    out: dict[str, str] = {}
    for k in sorted(groups, key=lambda k: k.startswith("ret:")):
        for r in groups[k]:
            for ip in ips_of(r["ip"]):
                out.setdefault(ip, k)
    return out


def _cia_side(g: list) -> dict:
    import system_report as sr
    cia = [r for r in g if not sr.is_off_book(r["asset_serial"])]
    first = lambda f: next(((r[f] or "").strip() for r in cia if (r[f] or "").strip()), "")  # noqa: E731
    return {"host": first("hostname"), "os": first("os_cia") or first("os"), "env": first("environment"),
            "owner": "、".join(dict.fromkeys(x for r in cia for x in ((r["owner"] or "").strip(), (r["custodian"] or "").strip()) if x)),
            "purpose": first("asset_purpose"), "room": first("physical_location"), "rack": first("rack_no")}


def _scan_side(g: list, m: dict | None) -> dict:
    host = next(((r["hostname_observed"] or "").strip() for r in g if (r["hostname_observed"] or "").strip()), "")
    os_ = (m or {}).get("os_text") or (m or {}).get("os_ver") if (m or {}).get("os_src") == "實測" else ""   # 只有納管實測才算「實掃」
    return {"host": host, "os": os_ or ""}


def _row_diffs(sh: dict, cia: dict, scan: dict) -> list[dict]:
    """欄位不一致：兩邊都有值、而且確定不同才算（查不到的不算不一致）。"""
    import system_stats
    out = []

    def add(field, a, b, c, why):
        out.append({"field": field, "label": FIELD_LABEL[field], "sheet": a, "cia": b, "scan": c, "why": why})

    hs, hc, hn = _short_host(sh.get("host")), _short_host(cia["host"]), _short_host(scan["host"])
    others = [x for x in (hc, hn) if x]
    if hs and others and any(x != hs for x in others):
        add("host", sh.get("host"), cia["host"], scan["host"], "主機名不同")
    so = f"{sh.get('os', '')} {sh.get('os_ver', '')}".strip()
    fs = os_family(so)
    for other, src in ((scan["os"], "實掃"), (cia["os"], "CIA")):
        fo = os_family(other)
        if fs and fo and (fs != fo or (_win_year(so) and _win_year(other) and _win_year(so) != _win_year(other))):
            add("os", so, cia["os"], scan["os"], f"表與{src}的作業系統不同")
            break
    es, ec = env_of(sh.get("env")), env_of(cia["env"])
    if es and ec and es != ec:
        add("env", sh.get("env"), cia["env"], "", f"表寫{es}、CIA 寫{ec}")
    ns, nc = _names(f"{sh.get('ap_owner', '')} {sh.get('infra_owner', '')}"), _names(cia["owner"])
    if ns and nc and not (ns & nc):
        add("owner", "、".join(x for x in (sh.get("ap_owner"), sh.get("infra_owner")) if x), cia["owner"], "", "負責人沒有任何一位相同")
    ps, pc = sh.get("purpose") or "", cia["purpose"]
    if len(ps) >= 4 and len(pc) >= 4 and not (_bigrams(ps) & _bigrams(pc)):
        add("purpose", ps, pc, "", "用途文字完全對不上")
    rs = system_stats.normalize_location(sh.get("location")) if (sh.get("location") or "").strip() else ""
    rc = system_stats.normalize_location(cia["room"]) if cia["room"] else ""
    if rs and rc and rs != rc:
        add("room", f"{sh.get('location')} {sh.get('rack') or ''}".strip(), f"{cia['room']} {cia['rack']}".strip(), "", "機房不同")
    return out


def self_conflicts(sh: dict) -> list[str]:
    """同一列自己矛盾。"""
    out = []
    env = env_of(sh.get("env"))
    text = f"{sh.get('purpose') or ''} {sh.get('asset_name') or ''}"
    lv = sh.get("sys_level") or ""
    prod_lv = bool(re.search(r"第[一二三]類", lv))
    if (env == "正式" or (env is None and prod_lv)) and _TESTY.search(text):
        why = f"環境別寫「{sh.get('env')}」" if env == "正式" else f"系統等級是「{lv}」"
        out.append(f"{why}，用途或資訊資產名稱卻有「{_TESTY.search(text).group(0)}」")
    if env == "正式" and "測試" in lv:
        out.append(f"環境別寫「{sh.get('env')}」，系統等級卻是「{lv}」")
    elif env == "測試" and prod_lv:
        out.append(f"環境別寫「{sh.get('env')}」，系統等級卻是「{lv}」")
    # 「資產用途」跟「資訊資產名稱」文字對不上不算矛盾：資訊資產名稱多半寫的是業務系統名（例：示範期貨AP），實測 2,400 列都對不上
    return out


def analyze(conn) -> dict:
    """每一列一筆結果＋各類數字。清單與數字是同一份計算（數字＝那一類的列數）。"""
    b = last_batch(conn)
    if not b:
        return {"batch": None, "rows": [], "counts": {k: 0 for k in KINDS}, "field_counts": {}, "off_only": []}
    import oncall
    import v2_hosts
    groups, _of = oncall._groups(conn)
    ipk = _ip_map(groups)
    ms = {m["key"]: m for m in v2_hosts.machines(conn, include_retired=True)}
    off = {}
    for o in conn.execute("SELECT * FROM srv_sheet_off ORDER BY id"):
        off.setdefault(o["ip"], dict(o))
    out, seen_ips = [], set()
    for i, r in enumerate(conn.execute("SELECT * FROM srv_sheet_row ORDER BY row_no"), start=1):
        sh = dict(r)
        ips = [x for x in (sh["ips"] or "").split(",") if x]
        seen_ips.update(ips)
        key = next((ipk[x] for x in ips if x in ipk), None)
        m = ms.get(key) if key else None
        g = groups.get(key, []) if key else []
        cia, scan = _cia_side(g), _scan_side(g, m)
        import system_report as sr
        # 「有沒有 CIA」用全站同一把尺（2.1「未登錄 CIA」＝existence 的 has_cia）；這台不在偵測範圍時退回看有沒有 CIA 登記列
        has_cia = bool(m.get("has_cia")) if m and (m.get("status") or {}).get("key") != "unknown" else any(not sr.is_off_book(x["asset_serial"]) for x in g)
        o = next((off[x] for x in ips if x in off), None)
        kinds = []
        if not ips:
            kinds.append("no_ip")
        elif o:
            kinds.append("off")
        elif not has_cia:
            kinds.append("no_cia")
        diffs = _row_diffs(sh, cia, scan) if (key and not o) else []
        if diffs:
            kinds.append("diff")
        selfc = self_conflicts(sh)
        if selfc:
            kinds.append("self")
        out.append({
            "__no": i, "row_no": sh["row_no"], "ip": sh["ip"], "ips": sh["ips"], "host": sh["host"], "purpose": sh["purpose"],
            "asset_name": sh["asset_name"], "env": sh["env"], "os": f"{sh['os'] or ''} {sh['os_ver'] or ''}".strip(),
            "ap_owner": sh["ap_owner"], "infra_owner": sh["infra_owner"], "ap_unit": sh["ap_unit"], "kind": sh["kind"],
            "location": f"{sh['location'] or ''} {sh['rack'] or ''}".strip(), "marked_at": sh["marked_at"], "sheet_id": sh["sheet_id"],
            "machine_key": key, "machine_name": (m or {}).get("name"), "in_system": bool(key),
            "system_status": (m or {}).get("status_label") or ("系統查無此 IP" if not key else ""), "has_cia": has_cia,
            "off": bool(o), "off_date": (o or {}).get("off_date"), "off_handler": (o or {}).get("handler"),
            "kinds": kinds, "kind_text": "、".join(KINDS[k] for k in kinds) or "一致",
            "diffs": diffs, "diff_text": "；".join(f"{d['label']}：{d['why']}" for d in diffs), "self": selfc, "self_text": "；".join(selfc),
            "cia_host": cia["host"], "cia_os": cia["os"], "cia_env": cia["env"], "cia_owner": cia["owner"], "cia_purpose": cia["purpose"],
            "cia_room": f"{cia['room']} {cia['rack']}".strip(), "scan_host": scan["host"], "scan_os": scan["os"],
        })
    counts = {k: sum(1 for r in out if k in r["kinds"]) for k in KINDS if k != "all"}
    counts["all"] = len(out)
    counts["no_cia_in_system"] = sum(1 for r in out if "no_cia" in r["kinds"] and r["in_system"])
    fc = {f: sum(1 for r in out for d in r["diffs"] if d["field"] == f) for f in FIELD_LABEL}
    off_only = [dict(o) for ip, o in off.items() if ip not in seen_ips]
    return {"batch": b, "rows": out, "counts": counts, "field_counts": fc, "off_only": off_only, "kinds": KINDS, "fields": FIELD_LABEL}


def listing(conn, kind: str = "all", field: str | None = None, q: str | None = None, a: dict | None = None) -> dict:
    """清單（下鑽）：kind 篩類別、field 篩不一致的欄位、q 篩關鍵字。數字與 analyze 同一份（a＝已算好的 analyze，免得重算）。"""
    a = a or analyze(conn)
    rows = a["rows"]
    if kind and kind != "all":
        if kind not in KINDS:
            raise ValueError("kind 不對")
        rows = [r for r in rows if kind in r["kinds"]]
    if field:
        if field not in FIELD_LABEL:
            raise ValueError("field 不對")
        rows = [r for r in rows if any(d["field"] == field for d in r["diffs"])]
    if q and q.strip():
        t = q.strip().lower()
        rows = [r for r in rows if t in " ".join(str(r.get(k) or "") for k in ("ip", "ips", "host", "purpose", "asset_name", "ap_owner",
                                                                                "infra_owner", "machine_name", "env", "os")).lower()]
    return {**{k: v for k, v in a.items() if k != "rows"}, "rows": rows, "kind": kind, "field": field, "shown": len(rows)}


def no_cia_ips(conn) -> set[str]:
    """9.4 補登用：彙整表有、CIA 無（不含已關機）的 IP。"""
    return {r["ip"] for r in analyze(conn)["rows"] if "no_cia" in r["kinds"] and r["ip"]}
