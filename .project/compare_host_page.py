"""主機履歷頁（summary／profile）口徑逐欄比對：改版前後的完整輸出必須一模一樣（D3：加總＝下鑽、同一台數字不可變）。

用法（兩支程式各跑一次，各用自己的程式碼目錄，同一份「靜止」的資料庫拷貝，再比對輸出檔）：
  python .project/compare_host_page.py pick  --code <backend> --db <db> --out keys.json [--random 30 --top 10 --seed 1]
  python .project/compare_host_page.py dump  --code <backend> --db <db> --keys keys.json --out new.json [--mode live|stale]
  python .project/compare_host_page.py diff  a.json b.json

  pick：隨機 N 台＋CI 關聯（ci_edge）最多的前 M 台＋特殊案例（多登記／退役／無資料／VMware／人工覆寫／剛變更各 --extra 台）。
  dump --mode live ：每台各自即時算（舊程式也只能這樣）。
  dump --mode scope：每支端點包在請求範圍裡（正式機的實際呼叫方式，routers/v2._call）；--fns 可加 machine、sections。
  dump --mode stale：新程式專用——模擬正式機的請求序列：整批走 fleet_cache.stale_ok()＋請求範圍（第一台冷算、其後吃限時舊值），
                     輸出要跟 live 逐欄相等（資料庫沒動時，舊值＝即時值）。
  diff：回傳 0＝逐欄相等；非 0＝印出不同的鍵路徑。fleet_as_of／fleet_age_s 是新程式附加的標示欄，比對時忽略。
資料庫請先拷一份（sqlite3 backup）再用，不要對正式資料庫跑；也請設 PYTHONDONTWRITEBYTECODE=1 免得在程式目錄留下 __pycache__。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

IGNORE = {"fleet_as_of", "fleet_age_s"}


def _setup(code: str, db: str):
    os.environ["ASSET_DB_PATH"] = db
    sys.path.insert(0, code)


def cmd_pick(a) -> int:
    _setup(a.code, a.db)
    import db as _db
    import oncall

    conn = _db.get_connection()
    groups, of = oncall._groups(conn)
    keys = sorted(k for k in groups if not k.startswith("ret:"))
    rnd = random.Random(a.seed)
    pick = rnd.sample(keys, min(a.random, len(keys)))
    deg: dict[str, int] = {}
    rows = conn.execute(
        "SELECT n.asset_serial AS s, count(*) AS c FROM ci_edge e "
        "JOIN ci_node n ON n.node_id IN (e.src_node_id, e.dst_node_id) "
        "WHERE n.asset_serial IS NOT NULL GROUP BY n.asset_serial").fetchall()
    for r in rows:
        k = of.get(r["s"])
        if k and not k.startswith("ret:"):
            deg[k] = deg.get(k, 0) + r["c"]
    top = [k for k, _ in sorted(deg.items(), key=lambda kv: (-kv[1], kv[0]))[:a.top]]
    # 特殊案例（D3 逐欄等價驗收要涵蓋：多登記／退役／無資料／人工覆寫／虛擬化／剛變更）
    extra: dict[str, list[str]] = {}

    def _take(name, cands):
        cands = sorted(set(cands))
        extra[name] = rnd.sample(cands, min(a.extra, len(cands)))

    _take("multi", [k for k, g in groups.items() if not k.startswith("ret:") and len(g) >= 2])
    _take("retired", [k for k in groups if k.startswith("ret:")])
    has_data = set()
    for tbl in ("host_spec", "host_account", "host_package", "host_service"):
        try:
            has_data |= {r[0] for r in conn.execute(f"SELECT DISTINCT asset_serial FROM {tbl}")}
        except Exception:  # noqa: BLE001
            pass
    _take("nodata", [k for k, g in groups.items() if not k.startswith("ret:")
                     and not any(r["asset_serial"] in has_data for r in g)])
    import v2_views
    _take("vmware", [k for k, g in groups.items() if not k.startswith("ret:") and v2_views.type_badge(g[0])["code"] == "ESX"])
    ov = []
    for tbl, col in (("rack_state_override", "key"), ("rack_position_override", "key"), ("onboard_exempt", "asset_serial")):
        try:
            for r in conn.execute(f"SELECT {col} FROM {tbl}"):
                ov.append(r[0] if col == "key" else of.get(r[0]))
        except Exception:  # noqa: BLE001
            pass
    _take("override", [k for k in ov if k in groups])
    recent = [r["asset_serial"] for r in conn.execute(
        "SELECT asset_serial FROM hardware WHERE updated_at IS NOT NULL ORDER BY updated_at DESC LIMIT 60")]
    _take("recent_change", [of[s] for s in recent if s in of])
    out = list(dict.fromkeys(top + pick + [k for v in extra.values() for k in v]))
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"top": top, "random": pick, "extra": extra, "all": out}, f, ensure_ascii=False, indent=1)
    print(f"{len(out)} 台（關聯最多 {len(top)}＋隨機 {len(pick)}＋特殊案例 {sum(len(v) for v in extra.values())}："
          + "、".join(f"{k}{len(v)}" for k, v in extra.items()) + f"）→ {a.out}")
    return 0


def cmd_dump(a) -> int:
    _setup(a.code, a.db)
    import db as _db
    import fleet_cache
    import v2_sections
    import v2_views

    with open(a.keys, encoding="utf-8") as f:
        keys = json.load(f)["all"]
    out = {}
    stale = a.mode == "stale"
    fns = [x for x in a.fns.split(",") if x]
    call = {"summary": v2_sections.summary, "profile": v2_sections.profile,
            "machine": v2_views.machine, "sections": v2_sections.all_sections}
    for k in keys:
        res = {}
        for fn in fns:
            conn = _db.get_connection()          # 一支端點一個請求（各自一條連線，跟正式機一樣）
            try:
                if stale:
                    with fleet_cache.stale_ok() as rep:
                        with fleet_cache.request_scope(conn):
                            res[fn] = call[fn](conn, k)
                    res[fn + "_stale_used"] = rep["used"]
                elif a.mode == "scope":
                    with fleet_cache.request_scope(conn):    # 正式機每個 v2 端點都包在請求範圍裡（routers/v2._call）
                        res[fn] = call[fn](conn, k)
                else:
                    res[fn] = call[fn](conn, k)
            except Exception as exc:  # noqa: BLE001 - 兩版都要丟同一個錯才算一致
                res[fn] = {"error": f"{type(exc).__name__}: {exc}"}
            finally:
                conn.close()
        out[k] = res
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, sort_keys=True, default=str)
    if stale:
        print("STATS", fleet_cache.STATS)
    print(f"{len(out)} 台 → {a.out}")
    return 0


def _walk(a, b, path, diffs):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in IGNORE or str(k).endswith("_stale_used"):
                continue
            if k not in a or k not in b:
                diffs.append(f"{path}/{k}: 只有一邊有")
            else:
                _walk(a[k], b[k], f"{path}/{k}", diffs)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(f"{path}: 長度 {len(a)} != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            _walk(x, y, f"{path}[{i}]", diffs)
    elif a != b:
        diffs.append(f"{path}: {str(a)[:60]!r} != {str(b)[:60]!r}")


def _leaves(x) -> int:
    if isinstance(x, dict):
        return sum(_leaves(v) for v in x.values())
    if isinstance(x, list):
        return sum(_leaves(v) for v in x)
    return 1


def cmd_diff(a) -> int:
    with open(a.a, encoding="utf-8") as f:
        x = json.load(f)
    with open(a.b, encoding="utf-8") as f:
        y = json.load(f)
    diffs: list[str] = []
    _walk(x, y, "", diffs)
    print(f"比對 {len(x)} 台、{_leaves(x)} 個欄位值；不同 {len(diffs)} 處")
    for d in diffs[:40]:
        print("  ", d)
    return 1 if diffs else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("pick")
    p.add_argument("--code", required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--random", type=int, default=30)
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--extra", type=int, default=4, help="每種特殊案例各挑幾台（多登記／退役／無資料／VMware／人工覆寫／剛變更）")
    d = sp.add_parser("dump")
    d.add_argument("--code", required=True)
    d.add_argument("--db", required=True)
    d.add_argument("--keys", required=True)
    d.add_argument("--out", required=True)
    d.add_argument("--mode", choices=("live", "stale", "scope"), default="live")
    d.add_argument("--fns", default="summary,profile", help="要比哪幾支：summary,profile,machine,sections")
    f = sp.add_parser("diff")
    f.add_argument("a")
    f.add_argument("b")
    a = ap.parse_args()
    return {"pick": cmd_pick, "dump": cmd_dump, "diff": cmd_diff}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
