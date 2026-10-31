"""型號規格表（2026-10-22）：機櫃用電／重量／U 數的依據。

種子：model_specs_seed.json（只有型號規格與出處，沒有任何公司資料）。每次程式啟動後第一次用到時
把種子 upsert 進 model_spec 表的 `data` 欄；人工改的值存在 `manual` 欄，**重新灌種子不會蓋掉**。
畫面顯示＝data 再疊上 manual。

用電規則（使用者 2026-10-22 決定）：每台設備＝**一顆** PSU 的額定瓦數（不是加總）；
種子的 psu_max_w 取 200–240V AC 可用的最大 PSU（原始研究值留在 note）。

比對：hardware.device_model 先正規化（小寫、去空白／連字號／底線／換行），對 model 與 aliases。
對不到、或是籠統描述（is_generic，例「IBM AS400」「EMC Storage」）＝「未知規格」。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

SEED_PATH = Path(__file__).with_name("model_specs_seed.json")
FIELDS = ("vendor_model_name", "rack_u", "psu_max_w", "psu_count", "weight_kg", "plug", "plug_confidence",
          "depth_mm", "vendor_move_required", "vendor_move_reason", "confidence", "is_generic",
          "system_level", "note", "aliases", "sources")
NUMERIC = {"rack_u", "psu_max_w", "psu_count", "weight_kg", "depth_mm"}
_seeded_dbs: set[str] = set()


def norm_model(s: str | None) -> str:
    return re.sub(r"[\s\-_]+", "", (s or "").lower())


def ensure(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS model_spec ("
        " model TEXT PRIMARY KEY, data TEXT, manual TEXT,"
        " updated_at TEXT, updated_by TEXT)")
    db_id = str(conn.execute("PRAGMA database_list").fetchone()[2])
    if db_id in _seeded_dbs:
        return
    seed(conn)
    _seeded_dbs.add(db_id)


def seed(conn: sqlite3.Connection) -> int:
    """把種子 upsert 進 data 欄；manual 欄一律不動。回寫入筆數。"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS model_spec ("
        " model TEXT PRIMARY KEY, data TEXT, manual TEXT,"
        " updated_at TEXT, updated_by TEXT)")
    models = json.loads(SEED_PATH.read_text(encoding="utf-8"))["models"]
    for m in models:
        data = json.dumps({k: m.get(k) for k in FIELDS}, ensure_ascii=False)
        conn.execute(
            "INSERT INTO model_spec (model, data) VALUES (?, ?) "
            "ON CONFLICT(model) DO UPDATE SET data = excluded.data", (m["model"], data))
    conn.commit()
    return len(models)


def all_specs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure(conn)
    out = []
    for r in conn.execute("SELECT model, data, manual, updated_at, updated_by FROM model_spec ORDER BY model"):
        data = json.loads(r["data"]) if r["data"] else {}
        manual = json.loads(r["manual"]) if r["manual"] else {}
        spec = {"model": r["model"], **{k: data.get(k) for k in FIELDS}, **manual}
        spec["manual_fields"] = sorted(manual)
        spec["from_seed"] = bool(r["data"])
        spec["updated_at"] = r["updated_at"]
        spec["updated_by"] = r["updated_by"]
        spec["aliases"] = spec.get("aliases") or []
        out.append(spec)
    return out


def index(conn: sqlite3.Connection, specs: list[dict] | None = None) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for s in specs if specs is not None else all_specs(conn):
        for name in [s["model"], *(s.get("aliases") or [])]:
            n = norm_model(name)
            if n:
                idx.setdefault(n, s)
    return idx


def lookup(idx: dict[str, dict], device_model: str | None) -> dict | None:
    return idx.get(norm_model(device_model)) if device_model else None


def known_w(spec: dict | None) -> float | None:
    """這個規格能不能算電：有規格、不是籠統描述、有 PSU 瓦數。"""
    if not spec or spec.get("is_generic"):
        return None
    w = spec.get("psu_max_w")
    return float(w) if w not in (None, "") else None


def update(conn: sqlite3.Connection, model: str, values: dict, clear: list[str] | None, by: str) -> dict:
    """人工改值（存 manual 欄，重新灌種子不會蓋掉）。clear＝要退回種子值的欄位。型號不存在就新增一筆人工型號。"""
    ensure(conn)
    model = (model or "").strip()
    if not model:
        raise ValueError("型號不能空白")
    bad = [k for k in values if k not in FIELDS]
    if bad:
        raise ValueError(f"不能改的欄位：{'、'.join(bad)}")
    row = conn.execute("SELECT manual FROM model_spec WHERE model = ?", (model,)).fetchone()
    manual = json.loads(row["manual"]) if row and row["manual"] else {}
    for k, v in values.items():
        if k in NUMERIC and v not in (None, ""):
            try:
                v = float(v)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{k} 要是數字") from exc
        if k == "aliases" and isinstance(v, str):
            v = [x.strip() for x in v.split(",") if x.strip()]
        manual[k] = v
    for k in clear or []:
        manual.pop(k, None)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO model_spec (model, manual, updated_at, updated_by) VALUES (?,?,?,?) "
        "ON CONFLICT(model) DO UPDATE SET manual = excluded.manual, updated_at = excluded.updated_at, "
        "updated_by = excluded.updated_by",
        (model, json.dumps(manual, ensure_ascii=False) if manual else None, now, by))
    conn.commit()
    return next(s for s in all_specs(conn) if s["model"] == model)


def coverage(conn: sqlite3.Connection, ms: list[dict] | None = None) -> dict[str, Any]:
    """機櫃裡的實體設備，各 device_model 原值對到哪個規格（給管理頁看「哪些型號還沒規格」）。"""
    import rack_layout

    ms = ms if ms is not None else rack_layout.machines(conn)
    idx = index(conn)
    raw: dict[str, dict] = {}
    for m in ms:
        if m["kind"] != "rack":
            continue
        key = m["model"] or "（空白）"
        r = raw.setdefault(key, {"device_model": key, "count": 0, "spec": None, "keys": []})
        r["count"] += 1
        r["keys"].append(m["key"])
        s = lookup(idx, m["model"])
        r["spec"] = s["model"] if s else None
        r["generic"] = bool(s and s.get("is_generic"))
    rows = sorted(raw.values(), key=lambda r: -r["count"])
    return {"rows": rows, "matched": sum(r["count"] for r in rows if r["spec"] and not r["generic"]),
            "total": sum(r["count"] for r in rows)}
