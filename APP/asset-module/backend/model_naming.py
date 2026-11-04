"""機型的標準寫法（2026-10-28 使用者：「要規範：第一個得要什麼機型，例如 IBM S922，不然大家都會亂寫」）。

標準＝「廠牌 型號」，中間一個空白，全部大寫：IBM S922、DELL R760、HPE DL380 GEN10、CISCO C9300-48T-E。
現有資料很亂（同一台寫成 DELL R750／Dell R750、Cisco 後面接換行、只寫 DELL、HPE Server），
所以先管「以後新增的」：新增設備時一律過這裡——整理空白／換行、廠牌統一大小寫、型號一定要有。
既有資料不在這裡改（另外盤點）。廠牌清單在「系統設定 → 機型廠牌」自己增刪（存資料庫），不用改檔案。
"""
from __future__ import annotations

import json
import re

SETTING_KEY = "model_vendors"
EXAMPLE = "IBM S922、DELL R760、HPE DL380 GEN10、CISCO C9300-48T-E"
#: 第一次使用時的預設廠牌（之後在「系統設定 → 機型廠牌」自己增刪，存在資料庫 app_settings）
#: 每個廠牌：(名稱, 別名, 序號碼數)。碼數寫「7」或「7-8」，空白＝不限（只檢查是英數字）。這些碼數是常見規格的預設值，
#: 請依實際調整：Dell service tag 7 碼、IBM 機身序號 7 碼、HPE 10 碼、Lenovo 8 碼、Cisco 11 碼。
DEFAULT_SERIAL = {"DELL": "7", "IBM": "7", "HPE": "10", "LENOVO": "8", "CISCO": "11"}
DEFAULT_VENDORS = [
    ("DELL", []), ("HPE", ["HP Enterprise"]), ("HP", []), ("IBM", []), ("LENOVO", []), ("CISCO", []),
    ("FORTINET", []), ("ARUBA", []), ("ORACLE", []), ("EMC", []), ("JUNIPER", []), ("F5", []), ("ATEN", []),
    ("PALO ALTO", ["Paloalto"]), ("SYNOLOGY", []), ("HUAWEI", []), ("SUPERMICRO", []), ("FUJITSU", []),
    ("VMWARE", []), ("BROCADE", []), ("NETAPP", []), ("HITACHI", []), ("ARISTA", []), ("H3C", []),
    ("RARITAN", []), ("FORCEPOINT", []), ("RIVERBED", []), ("APC", []), ("EATON", []),
]


def _up(items: list[dict]) -> list[dict]:
    for x in items:
        x["name"] = x["name"].upper()
    return items


def load_list(conn) -> list[dict]:
    """廠牌清單 [{name, aliases}]：資料庫有設定就用設定，沒有就用預設。"""
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SETTING_KEY,)).fetchone()
    if row and row["value"]:
        try:
            lst = json.loads(row["value"])
            if isinstance(lst, list):
                return _up([{"name": str(x["name"]), "aliases": [str(a) for a in x.get("aliases", [])],
                             "serial": str(x.get("serial", "") or "")} for x in lst])
        except (ValueError, TypeError, KeyError):
            pass
    return [{"name": n, "aliases": list(a), "serial": DEFAULT_SERIAL.get(n, "")} for n, a in DEFAULT_VENDORS]


def save_list(conn, items: list[dict]) -> list[dict]:
    """存廠牌清單：名稱不可空、不可重複（不分大小寫、空白）；別名也不可跟別的廠牌撞。回整理後的清單。"""
    out, seen = [], {}
    for x in items:
        name = " ".join(str(x.get("name", "")).split()).upper()            # 2026-10-28 使用者：全部大寫
        if not name:
            raise ValueError("廠牌名稱不能空白")
        if any(o["name"] == name for o in out):
            raise ValueError(f"廠牌「{name}」重複了")
        aliases = []
        for a in x.get("aliases", []):
            a = " ".join(str(a).split())
            if a and _key(a) != _key(name) and a not in aliases:
                aliases.append(a)
        for k in [name, *aliases]:
            kk = _key(k)
            if not kk:
                raise ValueError(f"「{k}」不是有效的廠牌寫法")
            if kk in seen and seen[kk] != name:
                raise ValueError(f"「{k}」重複了（{seen[kk]} 已經用了）")
            seen[kk] = name
        serial = "".join(str(x.get("serial", "") or "").split())
        if serial and not re.fullmatch(r"\d{1,2}(-\d{1,2})?", serial):
            raise ValueError(f"「{name}」的序號碼數要寫成 7 或 7-8（數字），不能寫「{serial}」")
        if "-" in serial and int(serial.split("-")[0]) > int(serial.split("-")[1]):
            raise ValueError(f"「{name}」的序號碼數範圍寫反了：{serial}")
        out.append({"name": name, "aliases": aliases, "serial": serial})
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (SETTING_KEY, json.dumps(out, ensure_ascii=False)))
    conn.commit()
    return out


def vendors(conn) -> dict[str, str]:
    m: dict[str, str] = {}
    for x in load_list(conn):
        for k in [x["name"], *x["aliases"]]:
            m[_key(k)] = x["name"]
    return m


def _key(tok: str) -> str:
    return re.sub(r"[^a-z0-9一-鿿]", "", tok.lower())            # 中文廠牌（曙光）也算


def normalize(conn, raw: str | None) -> str:
    """回標準寫法；不合格丟 ValueError（訊息直接給使用者看）。"""
    text = " ".join((raw or "").split())               # 換行、連續空白都變成一個空白
    if not text:
        raise ValueError(f"機型要填，寫成「廠牌 型號」，例：{EXAMPLE}")
    vs = vendors(conn)
    toks = text.split(" ")
    canon, used = None, 0
    for n in (2, 1):                                    # 先試兩個字的廠牌（Palo Alto），再試一個字
        if len(toks) >= n and _key("".join(toks[:n])) in vs:
            canon, used = vs[_key("".join(toks[:n]))], n
            break
    if canon is None:
        # 「Dell-R760」「Dell_R760」這種連在一起的寫法：用第一段判斷
        first = re.split(r"[-_/]", toks[0], maxsplit=1)
        if len(first) == 2 and _key(first[0]) in vs and not (len(toks) > 1):
            canon, used, toks = vs[_key(first[0])], 1, [first[0], first[1]]
        else:
            raise ValueError(f"機型開頭要是廠牌（{'、'.join(sorted(set(vs.values())))}），寫成「廠牌 型號」，例：{EXAMPLE}")
    model = " ".join(toks[used:]).strip(" -_/")
    if len(model) < 2 or not re.search(r"\d", model):
        raise ValueError(f"機型要有型號（含數字）：只寫廠牌（{canon}）或泛稱（例：{canon} Server）都不行，例：{EXAMPLE}")
    return f"{canon} {model}".upper()                    # 機型一律全大寫（使用者 2026-10-28）


def cleanup_preview(conn) -> dict:
    """既有資料整理成「廠牌 型號」的前後對照（只看、不改資料）。每個不同的機型寫法一列，附台數。
    狀態：已標準（寫法已是標準，含大寫）／只差大小寫空白／可整理（廠牌認得，寫法會變）／要人處理（廠牌認不得或沒有型號）／虛擬機不適用。"""
    rows = []
    for r in conn.execute("SELECT device_model AS m, COUNT(*) AS n FROM hardware "
                          "WHERE COALESCE(TRIM(device_model),'') <> '' GROUP BY device_model"):
        raw = r["m"]
        low = " ".join(raw.split()).lower()
        if low.startswith(("(vm", "（vm", "vm-", "vmware")) or low == "vm":
            rows.append({"before": raw, "after": "", "count": r["n"], "status": "虛擬機不適用",
                         "why": "虛擬機沒有實體廠牌型號，不用整理成「廠牌 型號」"})
            continue
        try:
            after = normalize(conn, raw)
            if after == raw:
                st, why = "已標準", ""
            elif " ".join(raw.split()).upper() == after:
                st, why = "只差大小寫空白", "內容一樣，只是大小寫或空白不同"
            else:
                st, why = "可整理", "廠牌別名或連在一起的寫法，會統一成標準廠牌名"
        except ValueError as exc:
            after, st, why = "", "要人處理", str(exc)
        rows.append({"before": raw, "after": after, "count": r["n"], "status": st, "why": why})
    order = {"要人處理": 0, "可整理": 1, "只差大小寫空白": 2, "已標準": 3, "虛擬機不適用": 4}
    rows.sort(key=lambda x: (order[x["status"]], -x["count"], x["before"]))
    for i, x in enumerate(rows, 1):
        x["no"] = i
    summary: dict[str, dict] = {}
    for x in rows:
        s = summary.setdefault(x["status"], {"kinds": 0, "machines": 0})
        s["kinds"] += 1
        s["machines"] += x["count"]
    return {"rows": rows, "summary": summary, "total_kinds": len(rows), "total_machines": sum(x["count"] for x in rows)}


def is_standard(conn, raw: str | None) -> bool:
    try:
        return normalize(conn, raw) == (raw or "")
    except ValueError:
        return False


def vendor_of(conn, model_std: str) -> dict | None:
    """標準寫法機型（「Dell R760」）→ 它的廠牌那一筆（名稱／序號碼數）。"""
    best = None
    for x in load_list(conn):
        if model_std.upper().startswith(x["name"].upper() + " ") and (best is None or len(x["name"]) > len(best["name"])):
            best = x
    return best


def serial_rule_text(rule: dict | None) -> str:
    if not rule or not rule.get("serial"):
        return "英數字"
    lo, _, hi = rule["serial"].partition("-")
    return f"{lo}–{hi} 碼英數" if hi else f"{lo} 碼英數"


def normalize_serial(conn, model_std: str | None, raw: str | None) -> str:
    """序號（機身標籤／service tag）標準化：去空白與連字號、一律大寫；只能是英數字；該廠牌有碼數規定就要符合。
    空白＝沒填（回空字串）。不合格丟 ValueError（訊息直接給使用者看）。"""
    text = re.sub(r"[\s\-_.]", "", (raw or "")).upper()
    if not text:
        return ""
    if not re.fullmatch(r"[A-Z0-9]+", text):
        raise ValueError("序號只能是英文字母和數字（空白、連字號會自動去掉）")
    rule = vendor_of(conn, model_std or "") if model_std else None
    if rule and rule.get("serial"):
        lo, _, hi = rule["serial"].partition("-")
        lo_n, hi_n = int(lo), int(hi or lo)
        if not (lo_n <= len(text) <= hi_n):
            raise ValueError(f"{rule['name']} 的序號是 {serial_rule_text(rule)}（你寫的 {text} 有 {len(text)} 碼）；"
                             "廠牌的碼數規定可以在「系統設定 → 機型廠牌」調整")
    elif len(text) < 5:
        raise ValueError("序號至少 5 碼")
    return text
