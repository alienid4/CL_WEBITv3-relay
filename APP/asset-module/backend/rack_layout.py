"""機櫃：系統主機部署圖的「機櫃」頁簽、4-5 機房架構圖、機櫃反查共用的一份計算。

資料只有兩欄：hardware.physical_location（機房）與 hardware.rack_no（機櫃／U 位）。
rack_no 寫法很雜（「M10/31U」「C5/34-35U」「1-1櫃(A1)/10U」「M03-M04」「218號-4F」「(VM)」…），
所以 parse_rack() 只認看得懂的幾種，**認不出來就保留原值、不猜**（rack=None, kind='unparsed'）。

一台＝system_stats.machine_key（同一台多筆登記只算一台，退役不列）。
VM（rack_no 寫「(VM)」或掛在 ESXi 上）不在機櫃裡：機房架構圖排除並計數；
系統主機部署圖的機櫃頁簽則讓 VM 沿用它所在 ESXi 主機的機櫃（標「經 ESXi xxx」）。

樓層：資料裡沒有。留 app_settings 掛鉤：
  rack_floor_map  JSON {"機櫃代碼前綴": "樓層"}，空的就不顯示樓層
  rack_floor_layout JSON（日後機櫃在樓面的排／位置），目前只原樣回傳，不畫假座標
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

DEFAULT_RACK_U = 42           # 機櫃高度沒有資料，畫立面圖用 42U（可由 app_settings rack_height_u 改）
_NA = {"", "N/A", "NA", "-", "--", "無", "NONE", "NULL"}

_RE_RACK_U = re.compile(r"^\s*(?P<rack>[^/]+?)\s*/\s*(?P<u1>\d{1,2})\s*(?:-\s*(?P<u2>\d{1,2}))?\s*U\s*(?P<rest>[、,，].*)?$", re.I)
_RE_RACK_ONLY = re.compile(
    r"^\s*(?P<rack>[A-Za-z]{1,4}-?\d{1,3}|\d[A-Za-z]\d{1,3}|\d{1,2}-{1,2}\d{1,2}\s*櫃?\s*(?:\([A-Za-z]\d{1,3}\))?)\s*櫃?\s*$")
_RE_FLOOR_RACK = re.compile(
    r"^\s*(?P<floor>\d{1,2})\s*F\s*機房\s*(?:(?P<a>\d{1,2})-{1,2}(?P<b>\d{1,2})|(?P<code>[A-Za-z]\d{1,3}))\s*$", re.I)
_RE_DASH_RACK = re.compile(r"^(\d{1,2})-{1,2}(\d{1,2})\s*櫃?\s*(?:\([A-Za-z]\d{1,3}\))?$")


def display_name(hostname: str | None, asset_name: str | None = None, fallback: str | None = None) -> str:
    """主機名是空的或「N/A」時改用資產名稱（CIA 很多機器主機名填 N/A）。"""
    h = (hostname or "").strip()
    if h.upper() in _NA:
        return (asset_name or "").strip() or h or (fallback or "")
    return h


def parse_rack(raw: str | None) -> dict[str, Any]:
    """rack_no → {kind, rack, u_start, u_end, raw}。kind：empty／vm／rack／unparsed。

    認得的寫法：「機櫃/起-迄U」（之後用「、」接的其他 U 段只保留在 raw）、純機櫃代碼（M11、A-9櫃、1-2櫃）。
    其餘（大樓／樓層、多櫃範圍「M03-M04」、「網路櫃」）一律 unparsed，畫面顯示原值。"""
    v = (raw or "").strip()
    if v.upper() in _NA:
        v = ""
    if not v:
        return {"kind": "empty", "rack": None, "u_start": None, "u_end": None, "raw": None}
    if v.upper() in ("(VM)", "VM", "（VM）"):
        return {"kind": "vm", "rack": None, "u_start": None, "u_end": None, "raw": v}
    m = _RE_RACK_U.match(v)
    if m:
        u1 = int(m.group("u1"))
        u2 = int(m.group("u2")) if m.group("u2") else u1
        lo, hi = min(u1, u2), max(u1, u2)
        return {"kind": "rack", "rack": _norm_rack(m.group("rack")), "u_start": lo, "u_end": hi, "raw": v}
    m = _RE_RACK_ONLY.match(v)
    if m:
        return {"kind": "rack", "rack": _norm_rack(m.group("rack")), "u_start": None, "u_end": None, "raw": v}
    m = _RE_FLOOR_RACK.match(v)
    if m:
        rack = _norm_rack(m.group("code")) if m.group("code") else f"{int(m.group('a'))}-{int(m.group('b'))}"
        return {"kind": "rack", "rack": rack, "u_start": None, "u_end": None, "raw": v,
                "floor_hint": f"{int(m.group('floor'))}F"}
    return {"kind": "unparsed", "rack": None, "u_start": None, "u_end": None, "raw": v}


def _norm_rack(r: str) -> str:
    """同一個櫃的不同寫法收成一種：B08＝B8、A-9＝A9、「1--4櫃(A4)」＝「1-4」。"""
    r = r.strip()
    m = _RE_DASH_RACK.match(r)
    if m:
        return f"{int(m.group(1))}-{int(m.group(2))}"
    r = r.rstrip("櫃").strip()
    m = re.fullmatch(r"([A-Za-z]+)-?(\d+)", r)
    if m:
        return f"{m.group(1).upper()}{int(m.group(2))}"
    return r.upper()


def rack_prefix(rack: str | None) -> str:
    """機櫃排（M10→M、C5→C、1-3→1）；認不出排就回「其他」。"""
    m = re.match(r"^([A-Za-z]+)\d", rack or "")
    if m:
        return m.group(1).upper()
    m = re.match(r"^(\d+)-\d+$", rack or "")
    return m.group(1) if m else "其他"


def _setting_json(conn: sqlite3.Connection, key: str) -> Any:
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return json.loads(r[0]) if r and r[0] else None
    except (sqlite3.OperationalError, json.JSONDecodeError, TypeError):  # 沒有表／值不是 JSON：當沒設定
        return None


def floor_of(floor_map: dict | None, rack: str | None) -> str | None:
    if not floor_map or not rack:
        return None
    best = None
    for prefix, floor in floor_map.items():
        if rack.upper().startswith(str(prefix).upper()) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, floor)
    return best[1] if best else None


def rack_height(conn: sqlite3.Connection) -> int:
    v = _setting_json(conn, "rack_height_u")
    try:
        return int(v) if v else DEFAULT_RACK_U
    except (TypeError, ValueError):
        return DEFAULT_RACK_U


OFF_STATUS = ("停用", "閒置")       # 有機櫃＝關機未下架
SCRAP_STATUS = ("報廢",)            # 有機櫃＝待確認（可能已搬走只是沒改資料）
STATE_LABEL = {"active": "在用", "off": "關機未下架", "pending": "待確認"}
_GARBLED = re.compile(r"\ufffd|[\u00c0-\u00ff]{2,}")


OVERRIDE_STATES = ("active", "off", "removed")
OVERRIDE_LABEL = {"active": "確認在用", "off": "標記已關機未下架", "removed": "標記已不在櫃上", None: "清除標記"}


def ensure_state_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS rack_state_override ("
        " key TEXT PRIMARY KEY, state TEXT NOT NULL, note TEXT, updated_by TEXT, updated_at TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS rack_state_audit ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, old_state TEXT, new_state TEXT,"
        " note TEXT, updated_by TEXT, updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))")


def set_state_override(conn: sqlite3.Connection, key: str, state: str | None, note: str | None, by: str,
                       commit: bool = True) -> dict:
    """機櫃現場盤點結果（人工標記，每次都寫稽核紀錄）：
      active＝確認在用（報廢但其實還在用）；off＝標記已關機未下架；removed＝標記已不在櫃上（從機櫃拿掉）；
      None＝清除標記，回到依資產狀態判斷。"""
    ensure_state_table(conn)
    if state is not None and state not in OVERRIDE_STATES:
        raise ValueError("state 只能是 active、off、removed 或清除")
    old = conn.execute("SELECT state FROM rack_state_override WHERE key = ?", (key,)).fetchone()
    if state is None:
        conn.execute("DELETE FROM rack_state_override WHERE key = ?", (key,))
    else:
        conn.execute(
            "INSERT INTO rack_state_override (key, state, note, updated_by, updated_at) VALUES (?,?,?,?,"
            "datetime('now','localtime')) ON CONFLICT(key) DO UPDATE SET state = excluded.state, "
            "note = excluded.note, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (key, state, note, by))
    conn.execute("INSERT INTO rack_state_audit (key, old_state, new_state, note, updated_by) VALUES (?,?,?,?,?)",
                 (key, old["state"] if old else None, state, note, by))
    if commit:
        conn.commit()
    return {"key": key, "state": state}


def set_state_bulk(conn: sqlite3.Connection, keys: list[str], state: str | None, note: str | None, by: str) -> dict:
    """一次套用現場盤點結果到多台（同一個交易；每台各一筆稽核紀錄）。"""
    if not keys:
        raise ValueError("沒有選任何設備")
    if state is not None and state not in OVERRIDE_STATES:
        raise ValueError("state 只能是 active、off、removed 或清除")
    for k in keys:
        set_state_override(conn, k, state, note, by, commit=False)
    conn.commit()
    return {"count": len(keys), "state": state}


def state_audit(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    ensure_state_table(conn)
    return [dict(r) for r in conn.execute("SELECT * FROM rack_state_audit ORDER BY id DESC LIMIT ?", (limit,))]


def machines(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """全站機器（machine_key 逐台）＋機房／機櫃解析＋所屬系統＋存在可信度＋型號規格＋機櫃狀態。

    機櫃狀態（實體在不在櫃上的口徑，2026-10-22 定案；跟全站台數用的存在可信度是兩回事）：
      - 使用中＋有機櫃＝在用（active）：算電、算 U、算重量、算機櫃台數，不管存在可信度看不看得到；
        看不到的另標「未觀察到」（unobserved）
      - 停用／閒置＋有機櫃＝關機未下架（off）；報廢＋有機櫃＝待確認（pending，可能還在櫃上）
      - 人工標記（rack_state_override，有稽核紀錄）：active／off／removed（已不在櫃上，從機櫃拿掉）
      off／pending 佔 U、算重量、用電算 0（另列「若全部開機 +X W」），不算台數。
    退役又沒有可判讀機櫃的機器不列（備查，不在任何機櫃裡）。
    型號寫「(VM)」卻填了機櫃的，不放進機櫃（kind='vm_model'，資料品質清單列出來）。"""
    import blast_radius
    import existence
    import model_specs
    import system_stats

    groups: dict[str, list] = {}
    for r in conn.execute(
            "SELECT asset_serial, hostname, ip, asset_name, api_id, physical_location, rack_no, asset_status, "
            "device_model, hw_serial, custodian, owner, pending_status, os, environment "
            "FROM hardware ORDER BY asset_serial"):
        k = system_stats.lifecycle_prefix(r) + system_stats.machine_key(r["hostname"], r["ip"], r["asset_serial"])
        groups.setdefault(k, []).append(r)
    ex = {m["key"]: m for m in existence.compute(conn)["machines"]}
    specs = model_specs.index(conn)
    ensure_state_table(conn)
    overrides = {r["key"]: dict(r) for r in conn.execute("SELECT * FROM rack_state_override")}
    ensure_position_table(conn)
    positions = {r["key"]: dict(r) for r in conn.execute(
        "SELECT * FROM rack_position_override WHERE status = 'active'")}
    newly_synced: list[tuple[str, dict]] = []
    out = []
    for k, rows in groups.items():
        retired = k.startswith("ret:")
        parsed = [parse_rack(r["rack_no"]) for r in rows]
        # 代表值：第一筆看得懂的機櫃；沒有就第一筆認不出的原值；都沒有才看是不是 VM
        pick = next((p for p in parsed if p["kind"] == "rack"), None) \
            or next((p for p in parsed if p["kind"] == "unparsed"), None) \
            or next((p for p in parsed if p["kind"] == "vm"), None) \
            or parsed[0]
        loc = next((r["physical_location"] for r in rows if (r["physical_location"] or "").strip()), None)
        cia = {"room": blast_radius.map_room(loc), "rack": pick["rack"] if pick["kind"] == "rack" else None,
               "u_start": pick["u_start"], "u_end": pick["u_end"], "raw": pick["raw"]}
        pos = positions.get(k)
        if pos and (cia["room"], cia["rack"], cia["u_start"], cia["u_end"]) == (
                pos["room"], pos["rack"], pos["u_start"], pos["u_end"]):
            newly_synced.append((k, pos))      # CIA 已經改成跟現場一樣：更正功成身退
            pos = None
        if pos:
            pick = {"kind": "rack", "rack": pos["rack"], "u_start": pos["u_start"], "u_end": pos["u_end"],
                    "raw": pick["raw"], "floor_hint": pick.get("floor_hint")}
        if retired and pick["kind"] != "rack":
            continue
        rep = rows[0]
        model = next((" ".join(r["device_model"].split()) for r in rows if (r["device_model"] or "").strip()), None)
        kind = pick["kind"]
        if kind == "rack" and model and model.strip().upper() in ("(VM)", "VM", "（VM）"):
            kind = "vm_model"
        statuses = sorted({(r["asset_status"] or "").strip() for r in rows})
        e = ex.get(k)
        seen = bool(e and e["counted"])
        state = None
        ov = overrides.get(k)
        if kind == "rack":
            if retired:
                state = "off" if any(st in OFF_STATUS for st in statuses) else "pending"
            else:
                state = "active"
            if ov and ov["state"] in ("active", "off"):
                state = ov["state"]
            elif ov and ov["state"] == "removed":
                kind = "removed"
                state = None
        spec = model_specs.lookup(specs, model)
        apis = sorted({(r["api_id"] or "").strip() for r in rows if (r["api_id"] or "").strip()})
        out.append({
            "key": k, "asset_serial": rep["asset_serial"], "serials": [r["asset_serial"] for r in rows],
            "name": display_name(rep["hostname"], rep["asset_name"], rep["asset_serial"]),
            "ip": rep["ip"], "room": pos["room"] if pos else cia["room"], "api_ids": apis,
            "position_corrected": bool(pos), "cia_position": cia if pos else None,
            "position_note": pos["note"] if pos else None, "position_by": pos["updated_by"] if pos else None,
            "position_at": pos["updated_at"] if pos else None,
            "model": model, "model_garbled": bool(model and _GARBLED.search(model)),
            "spec_model": spec["model"] if spec else None,
            "spec_generic": bool(spec and spec.get("is_generic")),
            "w": model_specs.known_w(spec),
            "kg": float(spec["weight_kg"]) if spec and spec.get("weight_kg") not in (None, "") and not spec.get("is_generic") else None,
            "system_level": spec.get("system_level") if spec else None,
            "vendor_move_required": bool(spec and spec.get("vendor_move_required")),
            "hw_serials": sorted({r["hw_serial"].strip() for r in rows if (r["hw_serial"] or "").strip()}),
            "custodian": next((r["custodian"].strip() for r in rows if (r["custodian"] or "").strip()), None),
            "owner": next((r["owner"].strip() for r in rows if (r["owner"] or "").strip()), None),
            "pending_status": next((r["pending_status"] for r in rows if (r["pending_status"] or "").strip()), None),
            "pending_confirm": bool(e and e.get("pending_confirm")),
            "asset_names": sorted({r["asset_name"] for r in rows if r["asset_name"]}),
            "asset_status": ",".join(x for x in statuses if x) or None, "retired": retired,
            "state": state, "state_label": STATE_LABEL.get(state or "", None),
            "override": ov["state"] if ov else None,
            "kind": kind, "rack": pick["rack"] if kind in ("rack", "removed") else None,
            "u_start": pick["u_start"] if kind == "rack" else None, "u_end": pick["u_end"] if kind == "rack" else None,
            "unobserved": kind == "rack" and state == "active" and not seen,
            "rack_raw": pick["raw"], "floor_hint": pick.get("floor_hint"),
            "exist_level": e["level"] if e else None, "exist_score": e["score"] if e else None,
            "exist_label": e["level_label"] if e else "不明", "seen": seen,
            "_oses": sorted({(r["os"] or "").strip() for r in rows if (r["os"] or "").strip()}),
            "_envs": sorted({(r["environment"] or "").strip() for r in rows if (r["environment"] or "").strip()}),
            "_hw_ips": [r["ip"] for r in rows if (r["ip"] or "").strip()],
        })
    if newly_synced:
        _mark_synced(conn, newly_synced)
    _attach_vm_systems(conn, out)
    _attach_type_level_ips(conn, out)
    out.sort(key=lambda m: (m["room"], m["rack"] or "", m["u_start"] or 0, m["name"].lower()))
    return out


# ===== 位置更正（現場盤點發現 U10 其實在 U11）=====
# 跟其他 *_override 一樣存自己的表，CIA 匯入不會蓋掉；CIA 之後改成跟更正一樣時自動標「CIA 已同步」、
# 不再套用（紀錄留著）。每次更正／清除都寫 rack_position_audit。

def ensure_position_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS rack_position_override ("
        " key TEXT PRIMARY KEY, asset_serial TEXT, room TEXT NOT NULL, rack TEXT NOT NULL,"
        " u_start INTEGER, u_end INTEGER, note TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',"
        " cia_room TEXT, cia_rack TEXT, cia_u_start INTEGER, cia_u_end INTEGER, cia_raw TEXT,"
        " updated_by TEXT, updated_at TEXT, synced_at TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS rack_position_audit ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, action TEXT NOT NULL,"
        " before_json TEXT, after_json TEXT, note TEXT, updated_by TEXT,"
        " updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))")


def _mark_synced(conn: sqlite3.Connection, items: list[tuple[str, dict]]) -> None:
    for k, pos in items:
        conn.execute("UPDATE rack_position_override SET status = 'synced', synced_at = datetime('now','localtime') "
                     "WHERE key = ? AND status = 'active'", (k,))
        conn.execute("INSERT INTO rack_position_audit (key, action, before_json, note, updated_by) "
                     "VALUES (?, 'cia_synced', ?, 'CIA 已同步：機櫃欄已跟現場更正一致', 'system')",
                     (k, json.dumps(pos, ensure_ascii=False)))
    conn.commit()


def _overlap(a1, a2, b1, b2) -> bool:
    return bool(a1 and b1) and a1 <= b2 and b1 <= a2


def collisions(ms: list[dict], key: str, room: str, rack: str, u_start: int | None, u_end: int | None) -> list[dict]:
    """同櫃 U 位重疊的其他設備（只警告、不擋）。"""
    if not u_start:
        return []
    return [{k: m[k] for k in ("key", "asset_serial", "name", "u_start", "u_end")}
            for m in ms if m["key"] != key and m["kind"] == "rack" and m["room"] == room and m["rack"] == rack
            and _overlap(u_start, u_end, m["u_start"], m["u_end"])]


def _validate_position(room: str, rack: str, u_start, u_height, note: str) -> tuple[str, str, int | None, int | None]:
    room, rack, note = (room or "").strip(), (rack or "").strip(), (note or "").strip()
    if not room or not rack:
        raise ValueError("機房和機櫃都要填")
    if not note:
        raise ValueError("要寫備註（例：10/23 現場盤點）")
    if u_start in (None, ""):
        return room, rack, None, None
    try:
        u1 = int(u_start)
        h = int(u_height or 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("U 位和 U 數要是整數") from exc
    if u1 < 1 or h < 1 or u1 + h - 1 > 60:
        raise ValueError("U 位超出範圍")
    return room, rack, u1, u1 + h - 1


def set_position(conn: sqlite3.Connection, key: str, room: str, rack: str, u_start, u_height, note: str, by: str,
                 ms: list[dict] | None = None, commit: bool = True) -> dict:
    """更正一台的位置（機房／機櫃／起始 U／U 數）。回這台更正後與同櫃 U 位重疊的設備（只警告）。"""
    ensure_position_table(conn)
    room, rack, u1, u2 = _validate_position(room, rack, u_start, u_height, note)
    rack = _norm_rack(rack)
    ms = ms if ms is not None else machines(conn)
    m = next((x for x in ms if x["key"] == key), None)
    if m is None:
        raise ValueError(f"查無此設備：{key}")
    cia = m["cia_position"] or {"room": m["room"], "rack": m["rack"] if m["kind"] == "rack" else None,
                                "u_start": m["u_start"], "u_end": m["u_end"], "raw": m["rack_raw"]}
    old = conn.execute("SELECT * FROM rack_position_override WHERE key = ?", (key,)).fetchone()
    conn.execute(
        "INSERT INTO rack_position_override (key, asset_serial, room, rack, u_start, u_end, note, status, cia_room, "
        "cia_rack, cia_u_start, cia_u_end, cia_raw, updated_by, updated_at, synced_at) "
        "VALUES (?,?,?,?,?,?,?,'active',?,?,?,?,?,?,datetime('now','localtime'),NULL) "
        "ON CONFLICT(key) DO UPDATE SET room=excluded.room, rack=excluded.rack, u_start=excluded.u_start, "
        "u_end=excluded.u_end, note=excluded.note, status='active', cia_room=excluded.cia_room, "
        "cia_rack=excluded.cia_rack, cia_u_start=excluded.cia_u_start, cia_u_end=excluded.cia_u_end, "
        "cia_raw=excluded.cia_raw, updated_by=excluded.updated_by, updated_at=excluded.updated_at, synced_at=NULL",
        (key, m["asset_serial"], room, rack, u1, u2, note, cia["room"], cia["rack"], cia["u_start"], cia["u_end"],
         cia["raw"], by))
    after = {"room": room, "rack": rack, "u_start": u1, "u_end": u2}
    conn.execute("INSERT INTO rack_position_audit (key, action, before_json, after_json, note, updated_by) "
                 "VALUES (?, 'set', ?, ?, ?, ?)",
                 (key, json.dumps(dict(old) if old else cia, ensure_ascii=False),
                  json.dumps(after, ensure_ascii=False), note, by))
    if commit:
        conn.commit()
    return {"key": key, **after, "collisions": collisions(ms, key, room, rack, u1, u2)}


def clear_position(conn: sqlite3.Connection, key: str, note: str | None, by: str) -> dict:
    ensure_position_table(conn)
    old = conn.execute("SELECT * FROM rack_position_override WHERE key = ?", (key,)).fetchone()
    if not old:
        raise ValueError("這台沒有位置更正")
    conn.execute("DELETE FROM rack_position_override WHERE key = ?", (key,))
    conn.execute("INSERT INTO rack_position_audit (key, action, before_json, note, updated_by) VALUES (?, 'clear', ?, ?, ?)",
                 (key, json.dumps(dict(old), ensure_ascii=False), note, by))
    conn.commit()
    return {"key": key, "cleared": True}


def set_positions_bulk(conn: sqlite3.Connection, items: list[dict], note: str, by: str) -> dict:
    """依盤點結果一次更正多台（同一份備註；全部寫完才算重疊，回每台的重疊清單）。"""
    if not items:
        raise ValueError("沒有要更正的設備")
    ms = machines(conn)
    for it in items:
        set_position(conn, it["key"], it["room"], it["rack"], it.get("u_start"), it.get("u_height"), note, by,
                     ms=ms, commit=False)
    conn.commit()
    after = machines(conn)
    by_key = {m["key"]: m for m in after}
    out = []
    for it in items:
        m = by_key.get(it["key"])
        out.append({"key": it["key"], "collisions": collisions(after, it["key"], m["room"], m["rack"], m["u_start"],
                                                                m["u_end"]) if m else []})
    return {"count": len(items), "results": out}


def position_audit(conn: sqlite3.Connection, key: str | None = None, limit: int = 200) -> list[dict]:
    ensure_position_table(conn)
    if key:
        rows = conn.execute("SELECT * FROM rack_position_audit WHERE key = ? ORDER BY id DESC LIMIT ?", (key, limit))
    else:
        rows = conn.execute("SELECT * FROM rack_position_audit ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


def _u_txt(a, b) -> str:
    return "" if not a else (f"{a}U" if a == b else f"{a}-{b}U")


def pending_cia_rows(conn: sqlite3.Connection) -> list[dict]:
    """「CIA 待更正」：還在套用（CIA 還沒改成一樣）的位置更正。欄名沿用機房搬遷盤點表。"""
    ms = {m["key"]: m for m in machines(conn)}      # 先跑一次，CIA 已同步的會被標掉
    out = []
    for r in conn.execute("SELECT * FROM rack_position_override WHERE status = 'active' ORDER BY updated_at"):
        m = ms.get(r["key"])
        out.append({"資產序號": r["asset_serial"], "主機名稱": m["name"] if m else "",
                    "原機房": r["cia_room"] or "", "原Rack": r["cia_rack"] or (r["cia_raw"] or ""),
                    "原U位": _u_txt(r["cia_u_start"], r["cia_u_end"]),
                    "新機房": r["room"], "新Rack": r["rack"], "新U位": _u_txt(r["u_start"], r["u_end"]),
                    "設備U數": (r["u_end"] - r["u_start"] + 1) if r["u_start"] else "",
                    "盤點人": r["updated_by"] or "", "現場盤點日期": (r["updated_at"] or "")[:10],
                    "備註": r["note"] or "", "CIA原值（機櫃欄）": r["cia_raw"] or ""})
    return out


PENDING_CIA_COLS = ["資產序號", "主機名稱", "原機房", "原Rack", "原U位", "新機房", "新Rack", "新U位", "設備U數",
                    "盤點人", "現場盤點日期", "備註", "CIA原值（機櫃欄）"]


def pending_cia_xlsx(conn: sqlite3.Connection):
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    rows = pending_cia_rows(conn)
    wb = Workbook()
    ws = wb.active
    ws.title = "CIA待更正"
    for c, name in enumerate(PENDING_CIA_COLS, 1):
        cell = ws.cell(1, c, name)
        cell.fill = PatternFill("solid", fgColor="00806A")
        cell.font = Font(color="FFFFFF", bold=True, size=10)
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[cell.column_letter].width = max(10, min(24, len(name) + 4))
    for i, r in enumerate(rows, 2):
        for c, name in enumerate(PENDING_CIA_COLS, 1):
            ws.cell(i, c, r[name])
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf, len(rows)


def room_volts(conn: sqlite3.Connection, room: str) -> float:
    """機房電壓（app_settings room_volts JSON {機房: V}；沒設定 220V）。"""
    v = (_setting_json(conn, "room_volts") or {}).get(room)
    try:
        return float(v) if v else 220.0
    except (TypeError, ValueError):
        return 220.0


def system_level_min(conn: sqlite3.Connection) -> int:
    try:
        return int(_setting_json(conn, "system_level_min_records") or 3)
    except (TypeError, ValueError):
        return 3


def rack_power(devs: list[dict], height: int, volts: float, sl_min: int = 3) -> dict[str, Any]:
    """一櫃的用電／散熱／重量／U。只有規格有 PSU 瓦數的在用設備算進用電（每台一顆 PSU）；
    其他在用設備是「未知規格」。關機未下架／待確認：用電 0，另列「若全部開機 +X W」；佔 U、算重量。
    工程系統（Exadata／ODA 這類 system_level）：同櫃同型號 ≥ sl_min 筆時，整套數字只算一次並標明。
    known_count + unknown_count = active_count。"""
    active = [d for d in devs if d["state"] == "active"]
    dormant = [d for d in devs if d["state"] in ("off", "pending")]
    sl_groups: dict[str, list] = {}
    for d in devs:
        if d.get("system_level") and d.get("spec_model"):
            sl_groups.setdefault(d["spec_model"], []).append(d)
    sl_used = {m: g for m, g in sl_groups.items() if len(g) >= sl_min}
    sl_keys = {d["key"] for g in sl_used.values() for d in g}
    w = 0.0
    known, unknown = [], []
    for d in active:
        if d["key"] in sl_keys:
            continue
        if d["w"] is not None:
            w += d["w"]
            known.append(d["key"])
        else:
            unknown.append(d["key"])
    sl_out = []
    for m, g in sl_used.items():
        sl = g[0]["system_level"] or {}
        sw = sl.get("psu_max_w")
        act = [d for d in g if d["state"] == "active"]
        if sw not in (None, "") and act:
            w += float(sw)
            known.extend(d["key"] for d in act)
        else:
            unknown.extend(d["key"] for d in act)
        sl_out.append({"model": m, "records": len(g), "w": sw, "kg": sl.get("weight_kg_max"), "u": sl.get("rack_u")})
    if_on = sum(d["w"] for d in dormant if d["w"] is not None and d["key"] not in sl_keys)
    kg = sum(d["kg"] for d in devs if d["kg"] is not None and d["key"] not in sl_keys)
    kg += sum(float(x["kg"]) for x in sl_out if x["kg"] not in (None, ""))
    occupied: set[int] = set()
    for d in devs:
        if d["u_start"]:
            occupied.update(range(d["u_start"], d["u_end"] + 1))
    best = run = 0
    for u in range(1, height + 1):
        run = 0 if u in occupied else run + 1
        best = max(best, run)
    used = len(occupied & set(range(1, height + 1)))
    return {
        "active_count": len(active), "off_count": sum(1 for d in dormant if d["state"] == "off"),
        "pending_count": sum(1 for d in dormant if d["state"] == "pending"),
        "w": round(w, 1), "amps": round(w / volts, 2) if volts else None, "btu_h": round(w * 3.412),
        "volts": volts, "kg": round(kg, 1), "if_all_on_w": round(if_on, 1),
        "used_u": used, "free_u": max(height - used, 0), "largest_free_u": best, "height_u": height,
        "known_count": len(known), "unknown_count": len(unknown), "unknown_keys": unknown,
        "unobserved_count": sum(1 for d in active if d.get("unobserved")),
        "unobserved_keys": [d["key"] for d in active if d.get("unobserved")],
        "active_keys": [d["key"] for d in active],
        "off_keys": [d["key"] for d in dormant if d["state"] == "off"],
        "pending_keys": [d["key"] for d in dormant if d["state"] == "pending"],
        "system_level": sl_out,
        "vendor_move": sorted({d["spec_model"] for d in devs if d.get("vendor_move_required") and d.get("spec_model")}),
    }


# ===== 4-1 平面圖的「顏色依」與網段（2026-10-23）=====
# 機器類型：只看資料（hardware.os、device_model、型號規格的 system_level、CI 圖的 ESXi 節點、san_switch），
# 對不到就是「不明」，不猜。順序有意義：一體機 > ESXi > 作業系統 > SAN > 儲存 > 網路 > Power 型號。
MACHINE_TYPES = ("esxi", "linux", "windows", "power", "oracle", "network", "san", "storage", "unknown")
MACHINE_TYPE_LABEL = {"esxi": "VM 主機（ESXi）", "linux": "實體 Linux", "windows": "實體 Windows",
                      "power": "AIX／Power", "oracle": "Oracle 一體機", "network": "網路設備",
                      "san": "SAN 交換器", "storage": "儲存", "unknown": "不明"}
_RE_ORACLE = re.compile(r"exadata|\bODA\b|oracle\s*database\s*appliance", re.I)
_RE_ESXI_OS = re.compile(r"esxi|vmware\s*esx", re.I)
_RE_AIX_OS = re.compile(r"\bAIX\b|VIOS|\bIBM\s*i\b|OS/?400", re.I)
_RE_WIN_OS = re.compile(r"windows", re.I)
_RE_LINUX_OS = re.compile(r"linux|red\s*hat|redhat|rhel|centos|rocky|debian|ubuntu|suse|sles|coreos|photon|alma", re.I)
# SAN 交換器：型號寫 SAN Switch／Brocade／Cisco MDS／Brocade 機型（DS-300B、G620…）；san_switch 表有這個 IP 也算
_RE_SAN = re.compile(r"san\s*switch|brocade|\bMDS\s*9\d{3}|\bDS-?\d{3}B\b|\bG[67]\d0\b", re.I)
_RE_STORAGE = re.compile(r"storage|flash\s*system|\bFS\d{4}\b|unity|powerstore|netapp|storwize|3par|"
                         r"\bDS8\d{3}\b|\bV[57]000\b|data\s*domain|avamar|pure\s*storage|isilon|powerscale|"
                         r"\bVNX\b|\bVMAX\b|tape|\bTS\d{4}\b|nimble", re.I)
_RE_NET = re.compile(r"cisco|aruba|fortinet|fortigate|\bFG-?\d|juniper|palo\s*alto|paloalto|\bPA-\d|\bF5\b|big-?ip|"
                     r"nexus|\bN9K|catalyst|\bWS-C|\bC9[0-9]{3}|\bISR\d|router|firewall|switch|"
                     r"\bAP\s*\d{3}\b|radware|check\s*point", re.I)
_RE_POWER_MODEL = re.compile(r"\bS9\d{2}\b|\bS10\d{2}\b|\bE9\d{2}\b|\bE10\d{2}\b|\bpower\s*\d|\bAS/?400\b", re.I)


def classify_machine_type(model: str | None, oses: list[str] | None = None, system_level: Any = None,
                          is_esxi: bool = False, is_san: bool = False) -> str:
    """一台實體機的類型（MACHINE_TYPES 其中一個）。認不出來回 'unknown'，不猜。"""
    md = model or ""
    os_txt = " ".join(oses or [])
    if system_level or _RE_ORACLE.search(md):
        return "oracle"
    if is_esxi or _RE_ESXI_OS.search(os_txt):
        return "esxi"
    if _RE_AIX_OS.search(os_txt):
        return "power"
    if _RE_WIN_OS.search(os_txt):
        return "windows"
    if _RE_LINUX_OS.search(os_txt):
        return "linux"
    if is_san or _RE_SAN.search(md):
        return "san"
    if _RE_STORAGE.search(md):
        return "storage"
    if "網路設備" in os_txt or _RE_NET.search(md):
        return "network"
    if _RE_POWER_MODEL.search(md):
        return "power"
    return "unknown"


SYS_LEVELS = ("core", "noncore", "unclassified")
SYS_LEVEL_LABEL = {"core": "核心", "noncore": "非核心", "unclassified": "未分級"}


def sys_class_level(sys_class: str | None) -> str:
    """business_system 的分類字串 → core／noncore／unclassified。
    221 實際值：「S1-第一類系統軟體（核心）」「S1-第一類系統軟體」「S2-…」「S3-…」、空白。
    S1／第一類／含「核心」＝核心；其他有寫的＝非核心；空白＝未分級。"""
    v = (sys_class or "").strip()
    if not v:
        return "unclassified"
    if "核心" in v or "第一類" in v or v.upper().startswith("S1"):
        return "core"
    return "noncore"


def machine_sys_level(api_ids, levels: dict[str, str]) -> str:
    """一台掛多個系統：取最高（核心 > 非核心 > 未分級）。"""
    got = {levels.get(a, "unclassified") for a in api_ids}
    for lv in SYS_LEVELS:
        if lv in got:
            return lv
    return "unclassified"


def _ipv4_int(ip: str | None) -> int | None:
    import ipaddress
    try:
        a = ipaddress.ip_address(str(ip or "").strip())
    except ValueError:
        return None          # 不是合法 IP（亂填、空白）＝沒有網段可對，不是程式錯誤
    return int(a) if a.version == 4 else None


class _SegIndex:
    """IP → 最小的那一段（network_segment，只認 IPv4）。一次讀進來、用 /16 分桶，避免逐 IP 查 SQL。"""

    def __init__(self, conn: sqlite3.Connection):
        self.buckets: dict[int, list[dict]] = {}
        self.wide: list[dict] = []
        rows = conn.execute("SELECT cidr, raw_cidr, net_start, net_end, purpose_desc, usage, vlan, environment, "
                            "environment_raw, location FROM network_segment WHERE net_start IS NOT NULL").fetchall()
        for r in rows:
            d = {"cidr": r["cidr"] or r["raw_cidr"], "start": r["net_start"], "end": r["net_end"],
                 "purpose": (r["purpose_desc"] or r["usage"] or "").strip() or None,
                 "vlan": (str(r["vlan"]).strip() if r["vlan"] not in (None, "") else None),
                 "env": r["environment"], "env_raw": r["environment_raw"], "location": r["location"]}
            b1, b2 = d["start"] >> 16, d["end"] >> 16
            if b2 - b1 > 64:
                self.wide.append(d)
            else:
                for b in range(b1, b2 + 1):
                    self.buckets.setdefault(b, []).append(d)

    def find(self, ip: str) -> dict | None:
        n = _ipv4_int(ip)
        if n is None:
            return None
        cand = [d for d in self.buckets.get(n >> 16, []) + self.wide if d["start"] <= n <= d["end"]]
        return min(cand, key=lambda d: d["end"] - d["start"]) if cand else None


def _split_ips(v: str | None) -> list[str]:
    return [x for x in re.split(r"[\s,;，、]+", v or "") if x and x.count(".") == 3]


def _attach_type_level_ips(conn: sqlite3.Connection, ms: list[dict]) -> None:
    """每台加：machine_type（＋label）、sys_level（＋label）、ips（全部 IP 與所屬網段）、envs（環境別）、os。
    IP＝hardware.ip（同一台的每筆登記）＋asset_nic.ip／ips（用這台全部資產序號去對）。"""
    # 這幾張表 db.init_db 都會建；查詢出錯就讓它報錯，不吞成「不明」（壞掉不能看起來像沒資料）
    def q(sql: str) -> list:
        return conn.execute(sql).fetchall()
    esxi_serials = {r[0] for r in q("SELECT asset_serial FROM ci_node WHERE node_type = 'esxi' AND gone_at IS NULL") if r[0]}
    san_ips = {(r[0] or "").strip() for r in q("SELECT ip FROM san_switch")}
    levels = {r["api_id"]: sys_class_level(r["sys_class_override"] or r["sys_class"])
              for r in q("SELECT api_id, sys_class, sys_class_override FROM business_system")}
    nic: dict[str, list[str]] = {}
    for r in q("SELECT asset_serial, ip, ips FROM asset_nic"):
        nic.setdefault(r["asset_serial"], []).extend([*_split_ips(r["ip"]), *_split_ips(r["ips"])])
    seg = _SegIndex(conn)
    for m in ms:
        ips: list[str] = []
        for ip in [*m.pop("_hw_ips", []), *(x for s in m["serials"] for x in nic.get(s, []))]:
            ip = (ip or "").strip()
            if ip and ip not in ips and not ip.startswith(("127.", "169.254.")):
                ips.append(ip)
        m["ips"] = []
        envs = set(m.pop("_envs", []))
        for ip in ips:
            d = seg.find(ip)
            m["ips"].append({"ip": ip, "cidr": d["cidr"] if d else None, "purpose": d["purpose"] if d else None,
                             "vlan": d["vlan"] if d else None, "env": d["env"] if d else None})
            if d:
                envs.update(x for x in (d["env"], d["env_raw"]) if x)
        m["envs"] = sorted(envs)
        oses = m.pop("_oses", [])
        m["os"] = ", ".join(oses) or None
        m["machine_type"] = classify_machine_type(
            m["model"], oses, m.get("system_level"),
            is_esxi=bool(esxi_serials & set(m["serials"])) or bool(m.get("vm_api_ids")),
            is_san=any(i in san_ips for i in ips))
        m["machine_type_label"] = MACHINE_TYPE_LABEL[m["machine_type"]]
        m["sys_level"] = machine_sys_level([*m["api_ids"], *m.get("vm_api_ids", [])], levels)
        m["sys_level_label"] = SYS_LEVEL_LABEL[m["sys_level"]]


def overlaps(devs: list[dict]) -> dict[str, list[str]]:
    """同櫃 U 位重疊：key → 跟它重疊的其他設備 key（只看有 U 位的）。"""
    out: dict[str, list[str]] = {}
    placed = [d for d in devs if d.get("u_start")]
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            if _overlap(a["u_start"], a["u_end"], b["u_start"], b["u_end"]):
                out.setdefault(a["key"], []).append(b["key"])
                out.setdefault(b["key"], []).append(a["key"])
    return out


def _attach_vm_systems(conn: sqlite3.Connection, ms: list[dict]) -> None:
    """ESXi 實體主機上跑的 VM 屬於哪些系統（vm_api_ids）：機房架構圖「標示系統」要連 VM 所在的櫃一起亮。
    VM→ESXi 走 CI 圖的 runs_on 邊，ESXi 節點的 asset_serial 對回實體機。"""
    by_serial = {s: m for m in ms for s in m["serials"]}
    esxi_serial = {r["node_id"]: r["asset_serial"] for r in conn.execute(
        "SELECT node_id, asset_serial FROM ci_node WHERE node_type = 'esxi' AND gone_at IS NULL")}
    for m in ms:
        m["vm_api_ids"] = []
    acc: dict[str, set] = {}
    for r in conn.execute("SELECT src_node_id, dst_node_id FROM ci_edge WHERE edge_type = 'runs_on' "
                          "AND gone_at IS NULL AND src_node_id LIKE 'hw:%'"):
        vm = by_serial.get(r["src_node_id"][3:])
        host = by_serial.get(esxi_serial.get(r["dst_node_id"]) or "")
        if vm and host and vm["api_ids"]:
            acc.setdefault(host["key"], set()).update(vm["api_ids"])
    for m in ms:
        m["vm_api_ids"] = sorted(acc.get(m["key"], set()) - set(m["api_ids"]))


def _system_names(conn: sqlite3.Connection, apis) -> dict[str, str]:
    import blast_radius

    return {a: blast_radius._system_name(conn, a) for a in apis}


def datacenter(conn: sqlite3.Connection, ms: list[dict] | None = None) -> dict[str, Any]:
    """4-5 機房架構圖：每個機房的機櫃磚（在用台數、系統數、已用 U、用電）、無機櫃資料、排除的 VM 數、
    資料品質清單。room 台數＝在用機櫃台數＋無機櫃資料台數（VM、關機未下架、待確認不算台數，另計）。"""
    ms = ms if ms is not None else machines(conn)
    height = rack_height(conn)
    floor_map = _setting_json(conn, "rack_floor_map") or {}
    sl_min = system_level_min(conn)
    rooms: dict[str, dict] = {}
    dq = {"vm_model": [], "garbled": [], "generic": [], "unknown_model": []}
    for m in ms:
        r = rooms.setdefault(m["room"], {"room": m["room"], "racks": {}, "no_rack": [], "vm_excluded": 0})
        if m["kind"] == "removed":
            t = r["racks"].setdefault(m["rack"], {"rack": m["rack"], "prefix": rack_prefix(m["rack"]),
                                                  "floor": floor_of(floor_map, m["rack"]), "devs": [],
                                                  "systems": set(), "vm_systems": set(), "removed": []})
            t["removed"].append(m["key"])
            continue
        if m["kind"] == "vm":
            r["vm_excluded"] += 1
            continue
        if m["kind"] == "vm_model":
            dq["vm_model"].append(_brief(m))
            continue
        if m["kind"] != "rack":
            if not m["retired"]:
                r["no_rack"].append(_brief(m))
            continue
        if m["model_garbled"]:
            dq["garbled"].append(_brief(m))
        elif m["spec_generic"]:
            dq["generic"].append(_brief(m))
        elif not m["spec_model"]:
            dq["unknown_model"].append(_brief(m))
        t = r["racks"].setdefault(m["rack"], {"rack": m["rack"], "prefix": rack_prefix(m["rack"]),
                                              "floor": floor_of(floor_map, m["rack"]), "devs": [],
                                              "systems": set(), "vm_systems": set(), "removed": []})
        t["devs"].append(m)
        if not t["floor"] and m.get("floor_hint"):
            t["floor"] = m["floor_hint"]          # 沒設樓層對照時，用機櫃欄自己寫的樓層（例「21F 機房 2-1」）
        if m["state"] == "active":
            t["systems"].update(m["api_ids"])
            t["vm_systems"].update(m["vm_api_ids"])
    out = []
    for r in rooms.values():
        volts = room_volts(conn, r["room"])
        tiles = []
        for t in sorted(r["racks"].values(), key=lambda t: (t["prefix"], _natural(t["rack"]))):
            pw = rack_power(t["devs"], height, volts, sl_min)
            pw["removed_count"] = len(t["removed"])
            pw["removed_keys"] = t["removed"]
            keys = [d["key"] for d in t["devs"] if d["state"] == "active"]
            ov = overlaps(t["devs"])
            tiles.append({"overlap_count": len(ov), "overlap_keys": sorted(ov), "all_keys": [d["key"] for d in t["devs"]],
                          "rack": t["rack"], "prefix": t["prefix"], "floor": t["floor"],
                          "device_count": len(keys), "system_count": len(t["systems"]),
                          "used_u": pw["used_u"], "height_u": height, "keys": keys,
                          "api_ids": sorted(t["systems"]), "vm_api_ids": sorted(t["vm_systems"] - t["systems"]),
                          "power": pw})
        if not tiles and not r["no_rack"] and not r["vm_excluded"]:
            continue
        racked = sum(t["device_count"] for t in tiles)
        tot = {k: round(sum(t["power"][k] for t in tiles), 1)
               for k in ("w", "kg", "if_all_on_w", "used_u", "free_u", "known_count", "unknown_count",
                         "off_count", "pending_count", "active_count", "unobserved_count", "removed_count")}
        tot["amps"] = round(tot["w"] / volts, 2) if volts else None
        tot["btu_h"] = round(tot["w"] * 3.412)
        tot["volts"] = volts
        tot["unknown_keys"] = [k for t in tiles for k in t["power"]["unknown_keys"]]
        tot["off_keys"] = [k for t in tiles for k in t["power"]["off_keys"]]
        tot["pending_keys"] = [k for t in tiles for k in t["power"]["pending_keys"]]
        tot["unobserved_keys"] = [k for t in tiles for k in t["power"]["unobserved_keys"]]
        tot["removed_keys"] = [k for t in tiles for k in t["power"]["removed_keys"]]
        out.append({"room": r["room"], "device_count": racked + len(r["no_rack"]), "racked": racked,
                    "racks": tiles, "no_rack": r["no_rack"], "vm_excluded": r["vm_excluded"], "power": tot})
    out.sort(key=lambda r: (r["room"] == "未填", -r["device_count"], r["room"]))
    return {"rooms": out, "height_u": height, "floor_map_set": bool(floor_map),
            "floor_layout": _setting_json(conn, "rack_floor_layout"),
            "total_racked": sum(r["racked"] for r in out),
            "total_devices": sum(r["device_count"] for r in out),
            "data_quality": {k: v for k, v in dq.items()},
            "devices": {m["key"]: _brief(m) for m in ms if m["kind"] in ("rack", "removed")},
            "system_level_min": sl_min}


def _brief(m: dict) -> dict:
    return {k: m.get(k) for k in ("key", "asset_serial", "name", "ip", "api_ids", "rack_raw", "model", "spec_model",
                                  "room", "rack", "u_start", "u_end", "state", "state_label", "asset_status", "w", "unobserved", "kind",
                                  "position_corrected", "cia_position",
                                  "exist_level", "exist_score", "exist_label", "seen", "override",
                                  "machine_type", "machine_type_label", "sys_level", "sys_level_label", "ips", "envs")}


def _natural(s: str) -> list:
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s or "")]


def rack_detail(conn: sqlite3.Connection, room: str, rack: str, highlight_api: str | None = None,
                ms: list[dict] | None = None) -> dict[str, Any]:
    """一個機櫃的立面：每台設備的 U 位、名稱、所屬系統、存在可信度；highlight_api 的設備標出來，
    其餘算「同櫃其他設備」。也就是機櫃反查（這個櫃裡有哪些系統）。"""
    ms = ms if ms is not None else machines(conn)
    devs = [m for m in ms if m["kind"] == "rack" and m["room"] == room and m["rack"] == rack]
    if not devs and not any(r["room"] == room and r["rack"] == rack for r in reservations(conn)):
        raise ValueError(f"查無此機櫃：{room} {rack}")
    apis = sorted({a for m in devs for a in m["api_ids"]})
    names = _system_names(conn, apis)
    height = rack_height(conn)
    items = []
    for m in devs:
        items.append({**{k: m[k] for k in ("key", "asset_serial", "name", "ip", "u_start", "u_end", "rack_raw", "model",
                                           "exist_level", "exist_score", "exist_label", "seen", "state",
                                           "state_label", "asset_status", "w", "kg", "spec_model", "override",
                                           "unobserved", "position_corrected", "cia_position", "position_note",
                                           "position_by", "position_at", "machine_type", "machine_type_label",
                                           "sys_level", "sys_level_label", "ips", "envs", "os")},
                      "overlap_with": [],
                      "systems": [{"api_id": a, "name": names[a]} for a in m["api_ids"]],
                      "mine": bool(highlight_api and highlight_api in m["api_ids"])})
    items.sort(key=lambda d: (-(d["u_end"] or 0), d["name"].lower()))
    ov = overlaps(items)
    for d in items:
        d["overlap_with"] = ov.get(d["key"], [])
    systems = [{"api_id": a, "name": names[a], "device_count": sum(1 for d in items if a in
                                                                    [s["api_id"] for s in d["systems"]])}
               for a in apis]
    floor_map = _setting_json(conn, "rack_floor_map") or {}
    floor = floor_of(floor_map, rack) or next((m["floor_hint"] for m in devs if m.get("floor_hint")), None)
    pw = rack_power(devs, height, room_volts(conn, room), system_level_min(conn))
    rsv = [r for r in reservations(conn) if r["room"] == room and r["rack"] == rack]
    return {"room": room, "rack": rack, "floor": floor, "height_u": height, "power": pw, "reservations": rsv,
            "devices": items, "systems": systems,
            "mine_count": sum(1 for d in items if d["mine"]),
            "others_count": sum(1 for d in items if not d["mine"]),
            "no_u_count": sum(1 for d in items if not d["u_start"])}


def system_racks(conn: sqlite3.Connection, smap: dict[str, Any], ms: list[dict] | None = None) -> dict[str, Any]:
    """系統主機部署圖「機櫃」頁簽：本系統每台主機落在哪個機房／機櫃／U。
    實體機用自己的機櫃；VM 沿用它所在 ESXi 主機（ESXi 的資產登記）的機櫃，標 via_esxi；
    都沒有＝「無機櫃資料」。每台只歸一組，組加總＝系統台數。"""
    ms = ms if ms is not None else machines(conn)
    by_serial = {}
    for m in ms:
        for s in m["serials"]:
            by_serial[s] = m
    esxi_serial = {e["node_id"]: e["asset_serial"] for e in smap["esxi"]}
    esxi_label = {e["node_id"]: e["label"] for e in smap["esxi"]}
    rooms: dict[str, dict] = {}
    no_rack: list[dict] = []
    for h in smap["hosts"]:
        own = next((by_serial[s] for s in h["serials"] if s in by_serial), None)
        src, via = None, None
        if own and own["kind"] == "rack":
            src = own
        else:
            for eid in h["esxi"]:
                em = by_serial.get(esxi_serial.get(eid) or "")
                if em and em["kind"] == "rack":
                    src, via = em, esxi_label.get(eid, eid)
                    break
        if not src:
            no_rack.append({"key": h["key"], "raw": own["rack_raw"] if own else None,
                            "via_esxi": [esxi_label.get(e, e) for e in h["esxi"]]})
            continue
        room = rooms.setdefault(src["room"], {"room": src["room"], "racks": {}})
        rk = room["racks"].setdefault(src["rack"], {"rack": src["rack"], "rows": []})
        rk["rows"].append({"key": h["key"], "u_start": src["u_start"], "u_end": src["u_end"],
                           "via_esxi": via, "rack_raw": src["rack_raw"]})
    floor_map = _setting_json(conn, "rack_floor_map") or {}
    height = rack_height(conn)
    sl_min = system_level_min(conn)
    out = []
    for r in rooms.values():
        racks = []
        for rk in sorted(r["racks"].values(), key=lambda x: _natural(x["rack"])):
            rk["rows"].sort(key=lambda x: (-(x["u_end"] or 0), x["key"]))
            devs = [m for m in ms if m["kind"] == "rack" and m["room"] == r["room"] and m["rack"] == rk["rack"]]
            racks.append({"rack": rk["rack"], "floor": floor_of(floor_map, rk["rack"]),
                          "host_count": len(rk["rows"]), "rows": rk["rows"],
                          "power": rack_power(devs, height, room_volts(conn, r["room"]), sl_min)})
        out.append({"room": r["room"], "host_count": sum(x["host_count"] for x in racks), "racks": racks})
    out.sort(key=lambda r: (-r["host_count"], r["room"]))
    return {"rooms": out, "no_rack": no_rack, "no_rack_count": len(no_rack),
            "racked_count": sum(r["host_count"] for r in out)}



def _count_racks(lst: list[dict]) -> dict[tuple, int]:
    out: dict[tuple, int] = {}
    for m in lst:
        if m["kind"] == "rack":
            out[(m["room"], m["rack"])] = out.get((m["room"], m["rack"]), 0) + 1
    return out


ANGLES = ("hardware", "host", "system")
ANGLE_LABEL = {"hardware": "硬體", "host": "主機", "system": "系統"}


def _pack(lst: list[dict], limit: int, systems=()) -> dict[str, Any]:
    racks: dict[tuple, int] = {}
    for m in lst:
        if m["kind"] == "rack":
            racks[(m["room"], m["rack"])] = racks.get((m["room"], m["rack"]), 0) + 1
    return {"total": len(lst),
            "matches": [{k: m[k] for k in ("key", "asset_serial", "name", "ip", "model", "room", "kind", "rack",
                                           "u_start", "u_end", "rack_raw", "api_ids", "exist_level",
                                           "exist_score", "exist_label", "seen")} for m in lst[:limit]],
            "racks": [{"room": r, "rack": k, "count": n} for (r, k), n in sorted(racks.items())],
            "systems": list(systems)}


def search(conn: sqlite3.Connection, q: str, ms: list[dict] | None = None, limit: int = 300) -> dict[str, Any]:
    """4-1 全域搜尋，一個框三個角度（不分大小寫、忽略空白連字號）：
      硬體：機型、硬體序號、資產序號
      主機：主機名、資產名稱、IP
      系統：APID 或系統名稱 → 該系統的全部實體設備
    VM 不列（不在機櫃裡）。每個角度各自回設備清單與「含符合設備的機櫃」，畫面分組顯示筆數。

    多個詞（空白／逗號分開，2026-10-23）：整串當一個詞**先找**，找得到就照舊（單詞行為不變，
    「IBM S922」這種帶空白的型號照樣整串比）；整串找不到、又有兩個以上的詞，才改成組合搜尋（search_multi）。"""
    import blast_radius

    all_ms = ms if ms is not None else machines(conn)
    single = _search_single(conn, q, all_ms, limit)
    terms = [t for t in re.split(r"[\s,，、;；]+", (q or "").strip()) if t]
    if len(terms) >= 2 and not any(single["counts"].values()) \
            and not single["angles"]["system"].get("racks_via_vm") and len(blast_radius.search_norm(q)) >= 2:
        return search_multi(conn, terms, all_ms, limit, q)
    return single


MULTI_FIELDS = ("system", "env", "model", "serial", "ip", "name")
MULTI_FIELD_LABEL = {"system": "系統", "env": "環境", "model": "機型", "serial": "序號", "ip": "IP", "name": "主機名"}


def search_multi(conn: sqlite3.Connection, terms: list[str], ms: list[dict], limit: int = 300,
                 q: str | None = None) -> dict[str, Any]:
    """組合搜尋：每個詞先看它打中哪個欄位（系統 > 環境 > 機型 > 序號 > IP > 主機名，取第一個當它的欄位；
    詞本身的結果＝它打中的所有欄位的聯集，不丟東西）。同欄位的詞 OR、不同欄位的詞 AND。
    例：「S922 S1022」＝機型 S922 或 S1022；「uat S922」＝UAT 環境的 S922。
    什麼都沒打中的詞列在 unknown（畫面顯示「找不到：xxx」），**不參與** AND，不會讓整個結果變空。"""
    import blast_radius

    norm = blast_radius.search_norm
    ms = [m for m in ms if m["kind"] not in ("vm", "removed", "vm_model")]
    apis = sorted({a for m in ms for a in [*m["api_ids"], *m.get("vm_api_ids", [])]})
    names = _system_names(conn, apis)
    by_key = {m["key"]: m for m in ms}
    info = []
    unknown = []
    for t in terms:
        tn = norm(t)
        if len(tn) < 2:
            unknown.append(t)
            continue

        def hit(texts):
            return any(tn in norm(x) for x in texts if x)

        sys_hit = {a for a in apis if tn in norm(a) or tn in norm(names[a])}
        hits = {
            "system": {m["key"] for m in ms if sys_hit & set(m["api_ids"])},
            "env": {m["key"] for m in ms if hit(m.get("envs", []))},
            "model": {m["key"] for m in ms if hit([m["model"]])},
            "serial": {m["key"] for m in ms if hit([*m["hw_serials"], *m["serials"]])},
            "ip": {m["key"] for m in ms if any(blast_radius.ip_match(x["ip"], t) for x in m.get("ips", []))
                   or blast_radius.ip_match(m["ip"], t)},
            "name": {m["key"] for m in ms if hit([m["name"], *m["asset_names"]])},
        }
        via = {m["key"] for m in ms if sys_hit & set(m.get("vm_api_ids", [])) and not (sys_hit & set(m["api_ids"]))}
        fields = [f for f in MULTI_FIELDS if hits[f]]
        if not fields and not via:
            unknown.append(t)
            continue
        field = fields[0] if fields else "system"
        info.append({"term": t, "field": field, "fields": fields, "keys": set().union(*hits.values()),
                     "via": via if field == "system" else set(), "systems": sorted(sys_hit)})
    groups: dict[str, set] = {}
    via_groups: dict[str, set] = {}
    for x in info:
        groups.setdefault(x["field"], set()).update(x["keys"])
        if x["field"] == "system":
            via_groups.setdefault("system", set()).update(x["via"])
    combined: set = set.intersection(*groups.values()) if groups else set()
    via_ok: set = set()
    if "system" in via_groups:
        others = [g for f, g in groups.items() if f != "system"]
        via_ok = via_groups["system"].intersection(*others) if others else set(via_groups["system"])
        via_ok -= combined
    order = {m["key"]: i for i, m in enumerate(ms)}
    lst = sorted((by_key[k] for k in combined), key=lambda m: order[m["key"]])
    pk = _pack(lst, limit, [{"api_id": a, "name": names[a]} for x in info for a in x["systems"]])
    pk["racks_via_vm"] = [{"room": r, "rack": k, "count": n} for (r, k), n in sorted(
        _count_racks([by_key[k] for k in via_ok]).items())]
    angles = {"hardware": {"label": "組合", **pk},
              "host": {"label": ANGLE_LABEL["host"], **_pack([], limit)},
              "system": {"label": ANGLE_LABEL["system"], **_pack([], limit), "racks_via_vm": []}}
    return {"q": q if q is not None else " ".join(terms), "mode": "multi", "angles": angles,
            "counts": {"hardware": len(lst), "host": 0, "system": 0},
            "multi": {"terms": [{"term": x["term"], "field": x["field"], "field_label": MULTI_FIELD_LABEL[x["field"]],
                                 "fields": [MULTI_FIELD_LABEL[f] for f in x["fields"]],
                                 "total": len(x["keys"]),
                                 "count": len(x["keys"] & combined),
                                 "keys": sorted(x["keys"] & combined, key=lambda k: order[k])} for x in info],
                      "unknown": unknown,
                      "rule": "同欄位 OR、不同欄位 AND"}}


def _search_single(conn: sqlite3.Connection, q: str, ms: list[dict], limit: int = 300) -> dict[str, Any]:
    import blast_radius

    norm = blast_radius.search_norm
    qn = norm(q)
    qi = (q or "").strip()
    empty = {a: {"label": ANGLE_LABEL[a], "total": 0, "matches": [], "racks": [], "systems": []} for a in ANGLES}
    if len(qn) < 2:
        return {"q": q, "angles": empty, "counts": {a: 0 for a in ANGLES}}
    ms = [m for m in ms if m["kind"] not in ("vm", "removed", "vm_model")]

    def hit_any(texts):
        return any(qn in norm(t) for t in texts if t)

    hw = [m for m in ms if hit_any([m["model"], *m["hw_serials"], *m["serials"]])]
    host = [m for m in ms if hit_any([m["name"], *m["asset_names"]]) or blast_radius.ip_match(m["ip"], qi)]
    apis = sorted({a for m in ms for a in m["api_ids"]})
    names = _system_names(conn, apis)
    sys_hit = [a for a in apis if qn in norm(a) or qn in norm(names[a])]
    sys_set = set(sys_hit)
    sysm = [m for m in ms if sys_set & set(m["api_ids"])]
    via = [m for m in ms if sys_set & set(m["vm_api_ids"]) and not (sys_set & set(m["api_ids"]))]

    angles = {
        "hardware": {"label": ANGLE_LABEL["hardware"], **_pack(hw, limit)},
        "host": {"label": ANGLE_LABEL["host"], **_pack(host, limit)},
        "system": {"label": ANGLE_LABEL["system"],
                   **_pack(sysm, limit, [{"api_id": a, "name": names[a]} for a in sys_hit]),
                   # 系統的 VM 所在的 ESXi 主機那幾櫃（設備本身不屬於這個系統，所以不算進 total）
                   "racks_via_vm": [{"room": r, "rack": k, "count": n} for (r, k), n in sorted(
                       _count_racks(via).items())]},
    }
    return {"q": q, "angles": angles, "counts": {a: angles[a]["total"] for a in ANGLES}}



# ===== 列印：A4 盤點表（一櫃一頁）與標籤貼紙 =====

def _u_text(m: dict) -> str:
    if not m["u_start"]:
        return ""
    return f"{m['u_start']}U" if m["u_start"] == m["u_end"] else f"{m['u_start']}-{m['u_end']}U"


def _marks(m: dict) -> list[str]:
    """盤點表上的標記：人工標的待 CIA 異動（例：關機未下架）、只有登記（看不到＝待確認）、只有 DY 看得到。"""
    out = []
    if m.get("state") == "off":
        out.append("關機未下架")
    elif m.get("state") == "pending":
        out.append("待確認")
    if m.get("pending_status"):
        out.append(str(m["pending_status"]))
    if m.get("unobserved"):
        out.append("未觀察到（只有登記）")
    elif m.get("pending_confirm"):
        out.append("待確認（只有 DY）")
    return out


def print_sheets(conn: sqlite3.Connection, room: str | None = None, rack: str | None = None,
                 api_id: str | None = None, ms: list[dict] | None = None) -> dict[str, Any]:
    """A4 盤點表資料。room+rack＝那一櫃；只給 room＝那個機房每一櫃；給 api_id＝這個系統碰到的每一櫃
    （含 VM 經 ESXi 的那櫃）。每一櫃列**櫃裡全部設備**（盤點看的是櫃子），U 由上往下，沒寫 U 的排最後。
    列數＝那一櫃的設備數（跟 rack_detail 同一份 machines）。"""
    import blast_radius

    ms = ms if ms is not None else machines(conn)
    if api_id:
        sm = blast_radius.system_map(conn, api_id)
        targets = [(r["room"], k["rack"]) for r in sm["racks"]["rooms"] for k in r["racks"]]
    elif room and rack:
        targets = [(room, rack)]
    elif room:
        targets = sorted({(m["room"], m["rack"]) for m in ms if m["kind"] == "rack" and m["room"] == room},
                         key=lambda t: (rack_prefix(t[1]), _natural(t[1])))
    else:
        raise ValueError("要指定機櫃、機房或系統")
    floor_map = _setting_json(conn, "rack_floor_map") or {}
    sheets = []
    for rm, rk in targets:
        devs = [m for m in ms if m["kind"] == "rack" and m["room"] == rm and m["rack"] == rk]
        devs.sort(key=lambda m: (m["u_start"] is None, -(m["u_end"] or 0), m["name"].lower()))
        floor = floor_of(floor_map, rk) or next((m["floor_hint"] for m in devs if m.get("floor_hint")), None)
        sheets.append({"room": rm, "rack": rk, "floor": floor, "device_count": len(devs), "rows": [{
            "u": _u_text(m), "model": m["model"], "serial": ", ".join(m["hw_serials"]) or None,
            "asset_serial": m["asset_serial"], "name": m["name"], "api_ids": m["api_ids"],
            "marks": _marks(m), "highlight": bool(api_id and api_id in m["api_ids"]),
        } for m in devs]})
    return {"sheets": sheets, "blank_rows": 5}


#: 標籤紙規格。使用者確認的紙：A4 24 格（3×8，70×37mm）。要加別種在 app_settings label_presets 放 JSON 陣列。
LABEL_PRESETS_DEFAULT = [
    {"id": "a4_3x8", "name": "A4 3×8（70×37mm）", "cols": 3, "rows": 8, "w_mm": 70, "h_mm": 37,
     "margin_top_mm": 0.5, "margin_left_mm": 0, "gap_x_mm": 0, "gap_y_mm": 0},
]


def label_presets(conn: sqlite3.Connection) -> list[dict]:
    v = _setting_json(conn, "label_presets")
    return v if isinstance(v, list) and v else LABEL_PRESETS_DEFAULT


def label_items(conn: sqlite3.Connection, room: str | None = None, rack: str | None = None,
                api_id: str | None = None, q: str | None = None, angle: str | None = None,
                ms: list[dict] | None = None) -> dict[str, Any]:
    """標籤貼紙要印的設備（只有實體設備；VM 貼不了貼紙）。來源跟畫面一樣：一櫃、一個機房、一個系統、或搜尋結果。"""
    ms = ms if ms is not None else machines(conn)
    phys = [m for m in ms if m["kind"] not in ("vm", "removed", "vm_model")]
    if q:
        res = search(conn, q, ms=ms, limit=100000)
        angles = [angle] if angle in ANGLES else list(ANGLES)
        keys = {x["key"] for a in angles for x in res["angles"][a]["matches"]}
        sel = [m for m in phys if m["key"] in keys]
    elif api_id:
        sel = [m for m in phys if api_id in m["api_ids"]]
    elif room and rack:
        sel = [m for m in phys if m["room"] == room and m["rack"] == rack and m["kind"] == "rack"]
    elif room:
        sel = [m for m in phys if m["room"] == room]
    else:
        raise ValueError("要指定機櫃、機房、系統或搜尋字")
    sel.sort(key=lambda m: (m["room"], m["rack"] or "~", -(m["u_end"] or 0), m["name"].lower()))
    return {"presets": label_presets(conn), "items": [{
        "key": m["key"], "name": m["name"], "contact": m["custodian"] or m["owner"],
        "contact_is_owner": not m["custodian"] and bool(m["owner"]), "ip": m["ip"],
        "asset_serial": m["asset_serial"], "room": m["room"], "rack": m["rack"], "u": _u_text(m),
    } for m in sel]}



# ===== 上架助手：找得下的機櫃＋預留（2026-10-23）=====
# 用的是跟機櫃正面同一份資料：在用（含未觀察到）、關機未下架、待確認都佔 U；預留也佔 U。
# 櫃裡有設備沒寫 U 位＝這櫃的空 U「不確定」，排後面並講原因。

def ensure_reservation_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS rack_reservation ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, room TEXT NOT NULL, rack TEXT NOT NULL,"
        " u_start INTEGER NOT NULL, u_end INTEGER NOT NULL, label TEXT NOT NULL, model TEXT,"
        " w REAL, kg REAL, note TEXT, expires_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',"
        " created_by TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),"
        " closed_by TEXT, closed_at TEXT)")


def reservations(conn: sqlite3.Connection, include_closed: bool = False) -> list[dict]:
    """預留中（含已過期但還沒取消的，標 expired）。"""
    ensure_reservation_table(conn)
    q = "SELECT * FROM rack_reservation" + ("" if include_closed else " WHERE status = 'active'") + " ORDER BY room, rack, u_start"
    today = datetime_now()[:10]
    return [{**dict(r), "expired": (r["expires_at"] or "") < today} for r in conn.execute(q)]


def datetime_now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def budgets(conn: sqlite3.Connection) -> dict:
    """機櫃上限：app_settings rack_budget＝{"default": {"w": 5000, "kg": null}, "racks": {"機房/機櫃": {"w":, "kg":}}}。
    沒設定時用電上限預設 5000 W，重量上限未設定。"""
    v = _setting_json(conn, "rack_budget") or {}
    d = {"w": 5000, "kg": None, **(v.get("default") or {})}
    return {"default": d, "racks": v.get("racks") or {}}


def _budget_for(b: dict, room: str, rack: str) -> dict:
    return {**b["default"], **(b["racks"].get(f"{room}/{rack}") or {})}


def _free_runs(occupied: set[int], height: int) -> list[tuple[int, int]]:
    runs, start = [], None
    for u in range(1, height + 1):
        if u not in occupied:
            start = u if start is None else start
        elif start is not None:
            runs.append((start, u - 1))
            start = None
    if start is not None:
        runs.append((start, height))
    return runs


def placement(conn: sqlite3.Connection, u: int, qty: int = 1, w: float | None = None, kg: float | None = None,
              rooms: list[str] | None = None, ms: list[dict] | None = None) -> dict[str, Any]:
    """找放得下 qty 台（每台 u 個 U、w 瓦、kg 公斤）的機櫃。每櫃回：放得下幾台、建議起始 U（最緊的連續空間優先）、
    用電／重量放了之後會不會超過上限、空 U 是否不確定，以及原因。"""
    if u < 1 or qty < 1:
        raise ValueError("U 數與台數都要 ≥ 1")
    ms = ms if ms is not None else machines(conn)
    height = rack_height(conn)
    b = budgets(conn)
    sl_min = system_level_min(conn)
    res = reservations(conn)
    racks: dict[tuple, list] = {}
    for m in ms:
        if m["kind"] == "rack":
            racks.setdefault((m["room"], m["rack"]), []).append(m)
    for r in res:
        racks.setdefault((r["room"], r["rack"]), [])
    out = []
    for (room, rack), devs in racks.items():
        if rooms and room not in rooms:
            continue
        occupied: set[int] = set()
        for d in devs:
            if d["u_start"]:
                occupied.update(range(d["u_start"], d["u_end"] + 1))
        rsv = [r for r in res if r["room"] == room and r["rack"] == rack]
        for r in rsv:
            occupied.update(range(r["u_start"], r["u_end"] + 1))
        no_u = [d for d in devs if not d["u_start"]]
        runs = _free_runs(occupied, height)
        fits = sum((e - s0 + 1) // u for s0, e in runs)
        cand = sorted([(e - s0 + 1, s0, e) for s0, e in runs if e - s0 + 1 >= u])
        best = cand[0] if cand else None
        pw = rack_power(devs, height, room_volts(conn, room), sl_min)
        rsv_w = sum(r["w"] or 0 for r in rsv)
        rsv_kg = sum(r["kg"] or 0 for r in rsv)
        bud = _budget_for(b, room, rack)
        need_w = (w or 0) * qty
        need_kg = (kg or 0) * qty
        w_after = pw["w"] + rsv_w + need_w
        kg_after = pw["kg"] + rsv_kg + need_kg
        reasons = []
        ok = fits >= qty
        if not ok:
            reasons.append(f"連續空間只放得下 {fits} 台（要 {qty} 台、每台 {u}U）")
        if no_u:
            reasons.append(f"有 {len(no_u)} 台沒寫 U 位，空 U 不確定")
        w_ok = None if bud.get("w") in (None, "") else w_after <= float(bud["w"])
        if w_ok is False:
            reasons.append(f"用電放了之後 {round(w_after)} W 超過上限 {bud['w']} W")
        if pw["unknown_count"]:
            reasons.append(f"有 {pw['unknown_count']} 台未知規格，用電可能更高")
        kg_ok = None if bud.get("kg") in (None, "") else kg_after <= float(bud["kg"])
        if kg_ok is False:
            reasons.append(f"重量放了之後 {round(kg_after)} kg 超過上限 {bud['kg']} kg")
        if w is None:
            reasons.append("型號用電未知，沒算用電")
        out.append({
            "room": room, "rack": rack, "fits": ok and w_ok is not False and kg_ok is not False,
            "fit_count": fits, "suggest_u_start": best[1] if best else None,
            "suggest_u_end": best[1] + u - 1 if best else None, "best_run": best[0] if best else 0,
            "leftover": (best[0] - u) if best else None, "free_u": height - len(occupied & set(range(1, height + 1))),
            "uncertain_u": bool(no_u), "no_u_count": len(no_u), "reserved_u": sum(r["u_end"] - r["u_start"] + 1 for r in rsv),
            "w_now": round(pw["w"] + rsv_w, 1), "w_after": round(w_after, 1), "w_budget": bud.get("w"), "w_ok": w_ok,
            "amps_after": round(w_after / pw["volts"], 2) if pw["volts"] else None,
            "kg_now": round(pw["kg"] + rsv_kg, 1), "kg_after": round(kg_after, 1), "kg_budget": bud.get("kg"), "kg_ok": kg_ok,
            "unknown_count": pw["unknown_count"], "reasons": reasons, "occupied": sorted(occupied),
        })
    out.sort(key=lambda r: (not r["fits"], r["uncertain_u"], r["leftover"] if r["leftover"] is not None else 999,
                            -(float(r["w_budget"]) - r["w_after"]) if r["w_budget"] not in (None, "") else 0,
                            r["room"], _natural(r["rack"])))
    return {"request": {"u": u, "qty": qty, "w": w, "kg": kg, "rooms": rooms or []}, "racks": out,
            "budget_default": b["default"], "height_u": height}


def reserve(conn: sqlite3.Connection, room: str, rack: str, u_start: int, u_height: int, label: str, expires_at: str,
            by: str, model: str | None = None, w: float | None = None, kg: float | None = None,
            note: str | None = None) -> dict:
    """預留一段 U（還沒登記的設備）。有到期日；跟現有設備或其他預留重疊只警告。"""
    ensure_reservation_table(conn)
    room, rack, label = (room or "").strip(), _norm_rack(rack or ""), (label or "").strip()
    if not room or not rack or not label:
        raise ValueError("機房、機櫃、名稱都要填")
    try:
        u1, h = int(u_start), int(u_height or 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("U 位和 U 數要是整數") from exc
    if u1 < 1 or h < 1 or u1 + h - 1 > 60:
        raise ValueError("U 位超出範圍")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expires_at or ""):
        raise ValueError("到期日格式要是 YYYY-MM-DD")
    u2 = u1 + h - 1
    ms = machines(conn)
    hit = collisions(ms, "", room, rack, u1, u2)
    hit += [{"key": f"rsv:{r['id']}", "asset_serial": None, "name": f"預留：{r['label']}", "u_start": r["u_start"],
             "u_end": r["u_end"]} for r in reservations(conn)
            if r["room"] == room and r["rack"] == rack and _overlap(u1, u2, r["u_start"], r["u_end"])]
    cur = conn.execute(
        "INSERT INTO rack_reservation (room, rack, u_start, u_end, label, model, w, kg, note, expires_at, created_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (room, rack, u1, u2, label, model, w, kg, note, expires_at, by))
    conn.commit()
    return {"id": cur.lastrowid, "collisions": hit}


def close_reservation(conn: sqlite3.Connection, rid: int, by: str) -> dict:
    ensure_reservation_table(conn)
    n = conn.execute("UPDATE rack_reservation SET status = 'cancelled', closed_by = ?, closed_at = datetime('now','localtime') "
                     "WHERE id = ? AND status = 'active'", (by, rid)).rowcount
    conn.commit()
    if not n:
        raise ValueError("查無這筆預留（或已取消）")
    return {"id": rid, "status": "cancelled"}


def find_assets(conn: sqlite3.Connection, q: str, limit: int = 20) -> list[dict]:
    """登記位置用：用資產序號／主機名找既有設備（實體機）。"""
    import blast_radius

    qn = blast_radius.search_norm(q)
    if len(qn) < 2:
        return []
    out = []
    for m in machines(conn):
        if m["kind"] in ("vm", "vm_model"):
            continue
        if any(qn in blast_radius.search_norm(t) for t in [m["name"], *m["serials"], *m["asset_names"]] if t):
            out.append({k: m[k] for k in ("key", "asset_serial", "name", "ip", "model", "room", "rack",
                                          "u_start", "u_end", "kind")})
        if len(out) >= limit:
            break
    return out
