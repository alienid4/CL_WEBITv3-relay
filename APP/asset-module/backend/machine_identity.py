"""台數口徑（Q79，2026-10-16 老賈決策／使用者授權）：一個主機名＝一台＋護欄。

## 規則
1. 有主機名：正規化（小寫、FQDN 取短名、去空白）後同名＝同一台；多 IP／多業務系統掛在底下。
2. 同名但硬體序號或 MAC 不只一種、或環境（正式／測試／備援）不只一種 → 不併、列「待確認」。
3. 預設／通用名（machine_identity.json：localhost、template…、含空白的設備型號）不當身分 → 現行規則。
4. 沒有主機名 → 現行規則（主機名＋IP；缺一或 0.0.0.0 退回序號）。

## 開關與停損
- app_settings.machine_key_mode：'hostname'＝新口徑、'legacy'＝現行。沒設＝legacy。
  **回退的主要方式**：系統設定 machine_key_mode=legacy（不改資料；標籤讀取同時看兩種口徑的 key，
  不會孤兒）。`python machine_identity.py rollback` 等於改設定＋記一筆判定＋把標籤搬回舊 key。
  環境變數 WEBIT3_MACHINE_KEY=legacy 也會強制舊規則（deploy.sh 重寫 unit 時會帶過去）。
- decide(conn) 只在 deploy／patch 跑 db.py 時呼叫一次（設定已有值就不再判）：用真實資料試算，
  停損 a 不同環境被併、b 總台數變動 > 10%、c 待確認 > 5% 任一成立 → 寫 legacy 並記錄原因；
  否則先留快照（每筆登記在舊規則下的 key）再切成 hostname。紀錄在 machine_key_switch 表，
  畫面（/api/version → 頂端提示一次、3-13 之後的「台數口徑」頁）讀它，絕不默默切換。
- 試算邏輯跟 scripts/machine_key_trial.py 各一份（公司機舊版要能跑），
  tests/asset_module/test_machine_key_trial.py 釘住兩邊一致。

## 「這台」怎麼算
system_stats.machine_key 先問 name_key()：新口徑且主機名可當身分、又不在待確認名單 → 'h:<名>'；
否則走舊規則。待確認名單要看整批資料才知道，所以 refresh(conn) 每次開連線時重算（db.get_connection）。
"""
from __future__ import annotations

import collections
import json
import os
import re
from pathlib import Path

CFG_PATH = Path(__file__).with_name("machine_identity.json")
MODE_SETTING = "machine_key_mode"
ENV_OVERRIDE = "WEBIT3_MACHINE_KEY"
MODE_HOSTNAME, MODE_LEGACY = "hostname", "legacy"

_CFG = json.loads(CFG_PATH.read_text(encoding="utf-8"))
GENERIC_NAMES = frozenset(_CFG["generic_names"])
GENERIC_RE = [re.compile(p, re.I) for p in _CFG["generic_patterns"]]
GUARD_ENV = tuple(_CFG["guard_env_groups"])
STOP = _CFG["stop_loss"]

#: 目前行程用的口徑（refresh 更新）。只有「名字→可不可以當身分」這件事，資料本身不動。
_STATE: dict = {"mode": MODE_LEGACY, "pending": frozenset(), "host_fix": {}}


def effective_hostname(hostname, asset_serial=None):
    """實測主機名校正（hostname_fix，決策 PRECEDENCE1）：CIA 主機名被判成打錯字的那幾筆，
    「同一台」的判斷改用實測主機名。CIA 欄位本身不改；沒有校正的原樣回傳。"""
    if asset_serial:
        fix = _STATE.get("host_fix") or {}
        h = fix.get(asset_serial)
        if h:
            return h
    return hostname


def _strip_domain(h) -> str:
    import system_stats

    return system_stats.strip_domain(h)


def norm_name(h) -> str:
    return _strip_domain((h or "").strip()).strip().lower()


def is_generic(n: str) -> bool:
    return (not n) or n in GENERIC_NAMES or any(p.search(n) for p in GENERIC_RE)


def _mac(v) -> str:
    return str(v or "").strip().lower().replace("-", ":").replace(".", ":")


def _active_rows(conn) -> list[dict]:
    import manage_state

    cols = {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}
    want = ["asset_serial", "hostname", "ip", "mac", "hw_serial", "environment", "asset_status"]
    sel = ", ".join(c if c in cols else f"NULL AS {c}" for c in want)
    return [dict(r) for r in conn.execute(f"SELECT {sel} FROM hardware")
            if (r["asset_status"] or "").strip() not in manage_state.RETIRED_STATUS]


def pending_names(rows: list[dict]) -> dict[str, list[str]]:
    """同名但 MAC／硬體序號／環境不只一種的名字 → 原因。這些名字不當身分（退回舊規則）。"""
    import manage_state

    by: dict = collections.defaultdict(list)
    for r in rows:
        n = norm_name(r.get("hostname"))
        if n and not is_generic(n):
            by[n].append(r)
    out: dict = {}
    for n, g in by.items():
        why = []
        macs = {_mac(r.get("mac")) for r in g if _mac(r.get("mac"))}
        sns = {str(r.get("hw_serial")).strip().lower() for r in g if str(r.get("hw_serial") or "").strip()}
        es = {manage_state.group_environment(r.get("environment")) for r in g} & set(GUARD_ENV)
        if len(macs) > 1:
            why.append(f"MAC {len(macs)} 種")
        if len(sns) > 1:
            why.append(f"硬體序號 {len(sns)} 種")
        if len(es) > 1:
            why.append("環境不同（" + "／".join(sorted(es)) + "）")
        if why:
            out[n] = why
    return out


def setting_mode(conn) -> str | None:
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (MODE_SETTING,)).fetchone()
    except Exception:  # noqa: BLE001 - 還沒有 app_settings（全新庫初始化途中）：當沒設
        return None
    return (r["value"] if r else None) or None


def effective_mode(conn) -> str:
    if os.environ.get(ENV_OVERRIDE, "").strip().lower() == MODE_LEGACY:
        return MODE_LEGACY
    return MODE_HOSTNAME if setting_mode(conn) == MODE_HOSTNAME else MODE_LEGACY


#: 待確認名單的結果快取：key＝「算它用到的所有輸入」的指紋（見 _pending_key），值＝frozenset。
#: 不是用時間或筆數當失效條件——指紋就是輸入本身，輸入一樣結果必然一樣，所以不會讀到舊值。
_PENDING_CACHE: "collections.OrderedDict[tuple, frozenset]" = collections.OrderedDict()
_PENDING_CACHE_MAX = 8      # 測試會開很多個臨時庫；正式機只有一個


def _pending_key(conn) -> tuple:
    """pending_names 的全部輸入的指紋。

    Q42（2026-10-17）：refresh 每開一條連線都跑，以前每次在 Python 把整張 hardware
    逐列轉 dict 再分組（221：4,788 筆約 22ms）。/adopt 一開約 16 支 API 同時打，
    單一 worker 下這 22ms × 16 全擠在 GIL 上（sqlite 逐列取資料會放開再搶 GIL，並行時更慢）。
    改成 SQL 一句把 pending_names 會讀的 5 個欄位原值串起來（quote() 讓串接不會有歧義）
    取雜湊——只回一列，C 層做完。資料任何一格有變，指紋就不同 → 重算；沒變才沿用。
    設定類輸入（通用名清單、護欄環境、環境分組設定）也一起進 key，測試改它們時不會沿用舊結果。
    """
    import hashlib
    import manage_state

    cols = {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}
    if not cols:
        raise LookupError("no hardware table")
    want = ["hostname", "mac", "hw_serial", "environment", "asset_status"]
    expr = "||','||".join(f"quote({c})" if c in cols else "'NULL'" for c in want)
    n, blob = conn.execute(
        f"SELECT count(*), group_concat(x, char(10)) FROM (SELECT {expr} AS x FROM hardware)").fetchone()
    digest = hashlib.blake2b((blob or "").encode("utf-8", "surrogatepass"), digest_size=20).hexdigest()
    cfg = json.dumps(manage_state._environment_config(), sort_keys=True, ensure_ascii=False)
    return (n, digest, tuple(sorted((_STATE.get("host_fix") or {}).items())),
            GUARD_ENV, id(GENERIC_NAMES), id(GENERIC_RE), len(GENERIC_RE),
            tuple(sorted(manage_state.RETIRED_STATUS)), cfg)


def _pending_cached(conn) -> frozenset:
    key = _pending_key(conn)
    hit = _PENDING_CACHE.get(key)
    if hit is not None:
        return hit
    rows = _active_rows(conn)
    for r in rows:
        r["hostname"] = effective_hostname(r.get("hostname"), r.get("asset_serial"))
    pend = frozenset(pending_names(rows))
    _PENDING_CACHE[key] = pend
    while len(_PENDING_CACHE) > _PENDING_CACHE_MAX:
        try:
            _PENDING_CACHE.popitem(last=False)
        except KeyError:  # 別的執行緒剛好也在清：清空了就好
            break
    return pend


def refresh(conn) -> None:
    """依這個資料庫重設口徑（db.get_connection 每次開連線都會叫）。舊規則時不掃資料。"""
    try:
        import hostname_fix

        fix = ({s: f["measured"] for s, f in hostname_fix.compute(conn)["fixes"].items()}
               if hostname_fix.enabled(conn) else {})
    except Exception:  # noqa: BLE001 - 校正算不出來（初始化途中／舊庫）：不校正，照原始主機名
        fix = {}
    _STATE["host_fix"] = fix
    try:
        mode = effective_mode(conn)
        pend = _pending_cached(conn) if mode == MODE_HOSTNAME else frozenset()
    except Exception:  # noqa: BLE001 - 還沒有 hardware 表（初始化途中）：維持舊規則，不擋開連線
        mode, pend = MODE_LEGACY, frozenset()
    changed = (_STATE.get("mode"), _STATE.get("pending"), _STATE.get("_fix_seen")) != (mode, pend, fix)
    _STATE["mode"], _STATE["pending"], _STATE["_fix_seen"] = mode, pend, fix
    if changed:
        _invalidate_dependents()


def _invalidate_dependents() -> None:
    """同一台的判斷變了（口徑／待確認名單／主機名校正）：依它算出來的快取要清掉。
    refresh 每開一條連線都會跑，所以只在真的變了才清，不然快取等於沒有。"""
    try:
        import observed_deps

        observed_deps.invalidate()
    except Exception:  # noqa: BLE001 - 清快取失敗不可以擋開連線；observed_deps 的簽章本身也含口徑
        pass


def name_key(hostname) -> str | None:
    """新口徑下這個主機名能當身分就回 'h:<名>'，否則 None（呼叫端走舊規則）。"""
    if _STATE["mode"] != MODE_HOSTNAME:
        return None
    n = norm_name(hostname)
    if not n or is_generic(n) or n in _STATE["pending"]:
        return None
    return "h:" + n


def mode() -> str:
    return _STATE["mode"]


# ---- 試算／停損（跟 scripts/machine_key_trial.py 同一套，測試釘住）----

#: 停損 a 的「環境」用原始值獨立判（不走 group_environment／護欄那一套），
#: 不然護欄已經擋掉環境不同，a 結構上永遠不會觸發（2026-10-17 審查 #10）。
_RAW_ENV = (("備援", ("備援", "災備", "dr")), ("正式", ("正式", "prod")),
            ("測試", ("測試", "uat", "dev", "test", "sit", "oa")))


def raw_env_class(v) -> str | None:
    t = str(v or "").strip().lower()
    if not t:
        return None
    for name, kws in _RAW_ENV:
        if any(k in t for k in kws):
            return name
    return None


MIN_MACHINES = int(_CFG.get("min_machines_to_decide", 100))


def key_fns(conn):
    """(舊規則 key, 主機名口徑 key) 兩個函式，吃 hardware 列 dict。待確認名單用這個庫的資料算。"""
    import system_stats

    pend = pending_names(_active_rows(conn))

    def okey(r):
        return system_stats.legacy_machine_key(r.get("hostname"), r.get("ip"), r.get("asset_serial"))

    def nkey(r):
        n = norm_name(r.get("hostname"))
        return "h:" + n if (n and not is_generic(n) and n not in pend) else okey(r)

    return okey, nkey, pend


def analyse(conn) -> dict:
    """舊規則 vs 新口徑的台數與停損判定。唯讀，不改目前行程的口徑。"""
    import manage_state

    rows = _active_rows(conn)
    okey, nkey, pend = key_fns(conn)
    cover: dict = collections.defaultdict(set)
    envs: dict = collections.defaultdict(set)
    members: dict = collections.defaultdict(list)
    for r in rows:
        k = nkey(r)
        cover[k].add(okey(r))
        members[k].append(r)
        e = manage_state.group_environment(r.get("environment"))
        if e in STOP["env_groups"]:
            envs[k].add(e)
    old_n = len({okey(r) for r in rows})
    new_n = len(cover)
    merged = sorted(k for k in cover if len(cover[k]) > 1)
    # 停損 a：最後合併出來的每一台，用原始環境值獨立判有沒有混到不同環境
    env_mixed = [k for k in merged
                 if len({c for c in (raw_env_class(m.get("environment")) for m in members[k]) if c}) > 1]
    pct = round((new_n - old_n) / old_n * 100, 2) if old_n else 0.0
    pend_pct = round(len(pend) / new_n * 100, 2) if new_n else 0.0
    stops = {
        "a": {"hit": len(env_mixed) > 0, "label": "不同環境（正式／測試／備援）被併成一台",
              "value": f"{len(env_mixed)} 組", "limit": "> 0 組"},
        "b": {"hit": abs(pct) > STOP["max_total_change_pct"], "label": "總台數變動",
              "value": f"{old_n}→{new_n}（{pct}%）", "limit": f"> ±{STOP['max_total_change_pct']}%"},
        "c": {"hit": pend_pct > STOP["max_pending_pct"], "label": "待確認組數佔總台數",
              "value": f"{len(pend)} 組（{pend_pct}%）", "limit": f"> {STOP['max_pending_pct']}%"},
    }
    return {"old": old_n, "new": new_n, "pct": pct, "stops": stops,
            "merged": [{"name": k[2:], "old_n": len(cover[k]), "envs": sorted(envs[k]),
                        "members": members[k]} for k in merged],
            "pending": [{"name": n, "why": "、".join(w),
                         "members": [r for r in rows if norm_name(r.get("hostname")) == n]}
                        for n, w in sorted(pend.items())],
            "generic_rows": sum(1 for r in rows if (r.get("hostname") or "").strip()
                                and is_generic(norm_name(r.get("hostname")))),
            "generic_members": [r for r in rows if (r.get("hostname") or "").strip()
                                and is_generic(norm_name(r.get("hostname")))],
            "retired_merged": retired_merged_count(conn, nkey)}


def retired_merged_count(conn, nkey) -> int:
    """退役登記跟同名的使用中機器拿到同一把 key 的筆數（審查 #8：待使用者決定，先只報數）。"""
    import manage_state

    rows = [dict(r) for r in conn.execute("SELECT asset_serial, hostname, ip, asset_status FROM hardware")]
    live = {nkey(r) for r in rows if (r["asset_status"] or "").strip() not in manage_state.RETIRED_STATUS}
    return sum(1 for r in rows if (r["asset_status"] or "").strip() in manage_state.RETIRED_STATUS
               and nkey(r) in live and nkey(r).startswith("h:"))


SWITCH_SQL = """
CREATE TABLE IF NOT EXISTS machine_key_switch (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    decided_at  TEXT NOT NULL,
    mode        TEXT NOT NULL,      -- hostname（切換）／legacy（停損或回退，用舊規則）
    old_n       INTEGER, new_n INTEGER,
    stops       TEXT,               -- JSON：a/b/c（＋verify／manual）的判定與數字
    snapshot    TEXT                -- 切換前每筆登記在舊規則下的 key（JSON {serial: key}）
)
"""


def last_decision(conn) -> dict | None:
    try:
        r = conn.execute("SELECT id, decided_at, mode, old_n, new_n, stops FROM machine_key_switch "
                         "ORDER BY id DESC LIMIT 1").fetchone()
    except Exception:  # noqa: BLE001 - 還沒判過（沒有這張表）
        return None
    if r is None:
        return None
    d = dict(r)
    d["stops"] = json.loads(d["stops"] or "{}")
    return d


def rekey_tags(conn, from_fn, to_fn) -> int:
    """機器標籤（asset_tag 存 machine_key 字串）跟著口徑換 key（審查 #3）。

    經由「序號」對照：每筆登記的 from_key → to_key。一把舊 key 對到多把新 key（回退時一台拆回多台）
    就每一把都掛一份；多把舊 key 併成一把（切換時多台併一台）同名標籤只留一份。對不到的不動。
    """
    try:
        tags = [dict(r) for r in conn.execute("SELECT * FROM asset_tag")]
    except Exception:  # noqa: BLE001 - 沒有 asset_tag 表＝沒有標籤要搬
        return 0
    mp: dict = collections.defaultdict(set)
    for r in conn.execute("SELECT asset_serial, hostname, ip FROM hardware"):
        d = dict(r)
        mp[from_fn(d)].add(to_fn(d))
    moved = 0
    for t in tags:
        targets = mp.get(t["machine_key"])
        if not targets or targets == {t["machine_key"]}:
            continue
        for k in targets:
            conn.execute("INSERT OR IGNORE INTO asset_tag (machine_key, tag, note, source, created_by, created_at) "
                         "VALUES (?,?,?,?,?,?)", (k, t["tag"], t["note"], t["source"], t["created_by"],
                                                  t["created_at"]))
        if t["machine_key"] not in targets:
            conn.execute("DELETE FROM asset_tag WHERE machine_key = ? AND tag = ?", (t["machine_key"], t["tag"]))
        moved += 1
    return moved


def _set_mode(conn, mode_: str) -> None:
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (MODE_SETTING, mode_))


def decide(conn) -> dict:
    """deploy／patch 時判一次：停損任一成立 → 留在舊規則；否則留快照、搬標籤後切成主機名口徑。

    設定已經有值（判過、或人工回退）就不再判。資料太少（舊規則台數 < min_machines_to_decide，
    例如全新安裝的空庫）**不記錄判定**，留給下一次有真實資料的部署／更新再判（審查 #2）。
    """
    import system_stats
    from db import _now_local

    conn.execute(SWITCH_SQL)
    already = setting_mode(conn)
    if already:
        return {"mode": already, "decided": False}
    a = analyse(conn)
    if a["old"] < MIN_MACHINES:
        return {"mode": None, "decided": False,
                "reason": f"資料太少（{a['old']} 台 < {MIN_MACHINES}），等有真實資料再判"}
    hit = [k for k, v in a["stops"].items() if v["hit"]]
    target = MODE_LEGACY if hit else MODE_HOSTNAME
    snap = None
    if target == MODE_HOSTNAME:
        snap = json.dumps({r["asset_serial"]: system_stats.legacy_machine_key(
            r["hostname"], r["ip"], r["asset_serial"]) for r in conn.execute(
            "SELECT asset_serial, hostname, ip FROM hardware")}, ensure_ascii=False)
        okey, nkey, _ = key_fns(conn)
        rekey_tags(conn, okey, nkey)
    conn.execute("INSERT INTO machine_key_switch (decided_at, mode, old_n, new_n, stops, snapshot) "
                 "VALUES (?,?,?,?,?,?)", (_now_local(), target, a["old"], a["new"],
                                         json.dumps(a["stops"], ensure_ascii=False), snap))
    _set_mode(conn, target)
    conn.commit()
    refresh(conn)
    _invalidate_dependents()
    return {"mode": target, "decided": True, "old": a["old"], "new": a["new"], "hit": hit}


def revert(conn, code: str, label: str, value: str) -> dict:
    """回到舊規則（切換後數字核對 ❌ 自動回退，或人工回退）：搬回標籤、記一筆判定、設定改 legacy。"""
    from db import _now_local

    conn.execute(SWITCH_SQL)
    okey, nkey, _ = key_fns(conn)
    rekey_tags(conn, nkey, okey)
    a = analyse(conn)
    stops = {code: {"hit": True, "label": label, "value": value, "limit": "—"}}
    conn.execute("INSERT INTO machine_key_switch (decided_at, mode, old_n, new_n, stops) VALUES (?,?,?,?,?)",
                 (_now_local(), MODE_LEGACY, a["old"], a["new"], json.dumps(stops, ensure_ascii=False)))
    _set_mode(conn, MODE_LEGACY)
    conn.commit()
    refresh(conn)
    _invalidate_dependents()
    return {"mode": MODE_LEGACY, "old": a["old"], "new": a["new"]}


def notice(conn) -> dict | None:
    """給 /api/version：最近一次判定（畫面頂端提示一次用）。mode 用實際生效的口徑（審查 #9）。"""
    d = last_decision(conn)
    if d is None:
        return None
    hit = {k: v for k, v in d["stops"].items() if v.get("hit")}
    eff = effective_mode(conn)
    if eff != d["mode"]:   # 人工回退（設定或環境變數）：不可以還說「已改為主機名」
        if os.environ.get(ENV_OVERRIDE, "").strip().lower() == MODE_LEGACY:
            hit = {**hit, "env": {"hit": True, "label": "伺服器環境變數強制舊規則",
                                  "value": f"{ENV_OVERRIDE}=legacy", "limit": "—"}}
        else:
            hit = {**hit, "setting": {"hit": True, "label": "系統設定改回舊規則（人工回退）",
                                      "value": f"{MODE_SETTING}={setting_mode(conn)}", "limit": "—"}}
    return {"id": d["id"], "decided_at": d["decided_at"], "mode": eff, "decided_mode": d["mode"],
            "effective_mode": eff, "old_n": d["old_n"], "new_n": d["new_n"],
            "hit": [{"code": k, **v} for k, v in hit.items()]}


def _verify_fails(db_file: Path) -> tuple[bool, list[str]]:
    """對某個資料庫檔跑 verify_numbers（子程序、乾淨狀態）。回 (有沒有跑完, ❌ 行)。

    「跑完」＝輸出有最後那行「════ 共 N 項」。子程序在頂層就炸掉（import 錯、開不了庫、被 OOM 砍）
    時沒有 ❌ 行也沒有總結行——**不可以**當成「沒有新 ❌」（2026-10-17 審查 M1）。
    環境變數 WEBIT3_MACHINE_KEY 一律拿掉：不然外面設了 legacy，主機名副本會用舊規則驗、假通過（L7）。
    """
    import subprocess
    import sys

    here = Path(__file__).with_name("verify_numbers.py")
    env = {k: v for k, v in os.environ.items() if k != ENV_OVERRIDE}
    env.update({"PYTHONIOENCODING": "utf-8", "ASSET_DB_PATH": str(db_file)})
    p = subprocess.run([sys.executable, str(here)], env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (p.stdout or "").splitlines()
    finished = any(ln.startswith("════ 共") for ln in out)
    return finished, [ln for ln in out if ln.startswith("❌")]


def _fail_key(line: str) -> str:
    """❌ 行的身分（頁＋項目名，數字拿掉），切換前後才比得起來。"""
    import re as _re

    k = _re.split(r"：App=|  〔|：", line, maxsplit=1)[0]
    return _re.sub(r"\d+", "#", k)


def _fail_gap(line: str) -> float | None:
    """❌ 行的落差（|App − 獨立|），沒有數字就 None。"""
    import re as _re

    m = _re.search(r"App=(-?[\d.]+).*?獨立=(-?[\d.]+)", line)
    if not m:
        return None
    try:
        return abs(float(m.group(1)) - float(m.group(2)))
    except ValueError:
        return None


def new_failures(base: list[str], after: list[str]) -> list[tuple[str, str | None]]:
    """切換造成的 ❌：基準沒有的項目，或同一項目但落差變大（審查 M2）。回 [(切換後那行, 基準那行或 None)]。"""
    b = {}
    for ln in base:
        b.setdefault(_fail_key(ln), ln)
    out = []
    for ln in after:
        old = b.get(_fail_key(ln))
        if old is None:
            out.append((ln, None))
            continue
        g0, g1 = _fail_gap(old), _fail_gap(ln)
        if g0 is not None and g1 is not None and g1 > g0:
            out.append((ln, old))
    return out


def _copy_db(src: Path, dst: Path) -> None:
    import sqlite3

    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)
    finally:
        s.close()
        d.close()


def _code_version() -> str:
    try:
        return json.loads(Path(__file__).with_name("version.json").read_text(encoding="utf-8"))["version"]
    except Exception:  # noqa: BLE001 - 讀不到版號就當未知（不擋判定，只是不能跳過重驗）
        return "?"


def _fingerprint(a: dict) -> str:
    return f"{_code_version()}|{a['old']}|{a['new']}"


def _record_failed(conn, r: dict, label: str, value: str, fp: str) -> None:
    from db import _now_local

    conn.execute(SWITCH_SQL)
    stops = {"verify": {"hit": True, "label": label, "value": value, "limit": "—", "fingerprint": fp}}
    conn.execute("INSERT INTO machine_key_switch (decided_at, mode, old_n, new_n, stops) VALUES (?,?,?,?,?)",
                 (_now_local(), MODE_LEGACY, r["old"], r["new"], json.dumps(stops, ensure_ascii=False)))
    conn.commit()


def decide_and_verify(db_path=None) -> int:
    """deploy.sh／patch.sh 呼叫。回 0＝正常（切換／不切換都算）；3＝試切後數字核對沒過（沒切換，警告）。

    **先在副本上判定＋驗證，過了才寫進正式庫**（審查 #1、N3、N5、M1–L7）：
    0. 先清掉上次被砍掉留下的暫存副本（L5）。
    1. 副本 A 上 decide()：停損成立／資料太少／已判過 → 照同樣結果處理正式庫，不需要數字核對。
    2. 會切成主機名：上次同一版程式、同樣台數已經驗失敗過 → 不重跑（M3：免得每次部署都多 1 分鐘又失敗）。
    3. 副本 B（舊規則）跑基準、A（主機名）再跑；任一份沒跑完＝失敗（M1）；只有新項目或落差變大才算切換造成（M2）。
    4. 都過了 → 再算一次正式庫的台數，跟副本一樣才切（L4：[3a] 期間舊服務可能還在寫）；不一樣下次再判。
    """
    import shutil
    import tempfile

    import db

    real = Path(db_path) if db_path else db.get_db_path()
    for stale in real.parent.glob(".mk_verify_*"):
        shutil.rmtree(stale, ignore_errors=True)
    tmp = Path(tempfile.mkdtemp(prefix=".mk_verify_", dir=str(real.parent)))
    try:
        a_file, b_file = tmp / "a.db", tmp / "b.db"
        _copy_db(real, a_file)
        ca = db.get_connection(a_file)
        try:
            r = decide(ca)
        finally:
            ca.close()
        if r.get("reason"):
            print(f"台數口徑：未判定（{r['reason']}）")
            return 0
        if not r["decided"]:
            print(f"台數口徑：{r['mode']}（已判過，不重判）")
            return 0
        conn = db.get_connection(real)
        try:
            if r["mode"] == MODE_LEGACY:
                r2 = decide(conn)
                if r2.get("decided") and r2["mode"] == MODE_LEGACY:
                    print(f"台數口徑維持舊規則（停損 {'、'.join(r2['hit'])}；試算 {r2['old']}→{r2['new']} 台）")
                else:
                    print(f"台數口徑：正式庫跟副本判的不一樣（{r2}），下次部署再判")
                return 0
            fp = f"{_code_version()}|{r['old']}|{r['new']}"
            last = last_decision(conn)
            if last and last["mode"] == MODE_LEGACY and \
                    (last["stops"].get("verify") or {}).get("fingerprint") == fp:
                print(f"!! 台數口徑：同一版程式、同樣台數（{r['old']}→{r['new']}）上次試切已驗失敗，這次不重跑；"
                      f"維持舊規則。要永久停在舊規則：系統設定 {MODE_SETTING}=legacy")
                return 3
            print(f"台數口徑試切主機名（{r['old']}→{r['new']} 台），在副本上比對切換前後的數字核對…")
            _copy_db(real, b_file)
            ok0, base = _verify_fails(b_file)
            ok1, after = _verify_fails(a_file)
            if base:
                print(f"   （切換前本來就有 {len(base)} 項 ❌，不算切換造成的：）")
                for ln in base[:10]:
                    print("     " + ln)
            if not (ok0 and ok1):
                which = "切換前（舊規則）" if not ok0 else "試切後（主機名）"
                _record_failed(conn, r, f"{which}數字核對沒跑完（未切換）", "沒有總結行", fp)
                print(f"!! {which}數字核對沒跑完（程式在頂層出錯或被中止）→ 不切換，維持舊規則。"
                      f"要永久停在舊規則：系統設定 {MODE_SETTING}=legacy")
                return 3
            new = new_failures(base, after)
            if new:
                _record_failed(conn, r, "試切主機名後數字核對多出 ❌（未切換）", f"{len(new)} 項", fp)
                print(f"!! 試切主機名後多出／變嚴重 {len(new)} 項 ❌ → 不切換（維持舊規則）。"
                      f"要永久停在舊規則：系統設定 {MODE_SETTING}=legacy")
                for ln, old in new[:10]:
                    print("   切換後 " + ln)
                    if old:
                        print("   切換前 " + old)
                return 3
            now = analyse(conn)
            if (now["old"], now["new"]) != (r["old"], r["new"]):
                print(f"台數口徑：驗證期間資料變了（副本 {r['old']}→{r['new']}，現在 {now['old']}→{now['new']}），"
                      "這次不切，下次部署再判")
                return 0
            r2 = decide(conn)
            if r2.get("decided") and r2["mode"] == MODE_HOSTNAME:
                print(f"台數口徑已改為主機名，前後 {r2['old']}→{r2['new']} 台（切換沒有造成新的 ❌）")
            elif r2.get("decided"):
                print(f"台數口徑維持舊規則（停損 {'、'.join(r2['hit'])}）")
            else:
                print(f"台數口徑：{r2.get('mode') or '未判定'}（{r2.get('reason') or '已判過'}）")
            return 0
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def rollback(db_path: str | None = None) -> int:
    """人工回退（主要回退方式，記在 app_settings、部署不會洗掉）：python machine_identity.py rollback"""
    import db

    conn = db.get_connection(Path(db_path) if db_path else None)
    try:
        r = revert(conn, "manual", "人工回退到舊規則", "machine_identity.py rollback")
        print(f"台數口徑已回到舊規則（{r['new']}→{r['old']} 台）")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    # 交給「import 進來的那一份」模組跑：system_stats／db 用的是 machine_identity（不是 __main__），
    # 兩份各有自己的 _STATE，用錯份口徑會不同步。路徑一律轉 Path（db.get_connection 要 Path）。
    import machine_identity as _mi

    _cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    _p = os.environ.get("ASSET_DB_PATH")
    _p = Path(_p) if _p else None
    if _cmd == "decide-verify":
        sys.exit(_mi.decide_and_verify(_p))
    if _cmd == "rollback":
        sys.exit(_mi.rollback(_p))
    print("用法：python machine_identity.py decide-verify | rollback")
    sys.exit(2)
