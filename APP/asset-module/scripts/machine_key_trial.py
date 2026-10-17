#!/usr/bin/env python3
"""台數口徑試算（Q79：一個主機名＝一台＋護欄）——唯讀、只印數字，不寫任何東西。

用法（裝新版之前，在公司機跑）：
    python3 machine_key_trial.py [asset.db 路徑]
    預設路徑：環境變數 ASSET_DB_PATH，否則 /opt/webit3/data/asset.db

為什麼是獨立檔（2026-10-16）：公司機還跑舊版，這支不能 import 新版模組，只用標準函式庫
（Python 3.11 可跑）。規則跟正式程式各寫一份，所以 tests/asset_module/test_machine_key_trial.py
把兩邊釘在一起：現行口徑＝system_stats.machine_key、環境分組＝manage_state.group_environment、
帳外前綴＝system_report.OFF_BOOK_PREFIXES、通用名清單＝backend/machine_identity.json，
任何一邊改了另一邊沒跟上，測試就紅。

輸出去識別化：主機名只留前 3 字＋***，IP 只留前兩段（a.b.*.*），可以直接貼出來。

新口徑（Q79）：
  1. 有主機名：正規化（小寫、去網域、去空白）後同名＝同一台；多個 IP／業務系統掛在底下。
  2. 同名但硬體序號或 MAC 不只一種，或環境（正式／測試／備援）不只一種 → 不併、列「待確認」
     （DR 複本、名稱重用、叢集名、vCenter VM 名跟測試機同名）。
  3. 預設／通用名（清單見 GENERIC_NAMES／GENERIC_PATTERNS）不當身分 → 退回現行規則。
  4. 沒有主機名（網路／儲存設備）→ 現行規則。
停損（任一成立就不切換）：a 不同環境（正式／測試／備援）被併成一台；b 總台數變動 > 10%；
c 待確認組數 > 總台數 5%。
"""
from __future__ import annotations

import collections
import os
import re
import sqlite3
import sys

# ---- 跟正式程式同步的常數（test_machine_key_trial.py 釘住）----
DOMAIN_RE = re.compile(r"^([^.]+)\.((?:[a-z0-9-]+\.)+[a-z]{2,})$", re.I)
RETIRED_STATUS = {"停用", "報廢", "閒置"}
OFF_BOOK_PREFIXES = ("DYN-", "VC-", "AUTO-")
# ＝backend/environment_groups.json（2026-10-17 加別名；test_machine_key_trial 釘住兩邊一樣）
ENV_GROUPS = [("正式", ["正式", "PROD", "Prod", "prod", "PRODUCTION", "Production", "production"]),
              ("備援", ["備援", "災備", "異地", "DR", "Dr", "dr"]),
              ("測試", ["測試", "UAT", "uat", "Uat", "DEV", "dev", "Dev", "SIT", "sit", "Sit",
                        "TEST", "Test", "test", "OA"])]
ENV_FALLBACK, ENV_EMPTY = "其他", "未填"
GENERIC_NAMES = {
    "localhost", "localhost.localdomain", "localdomain", "ubuntu", "debian", "rhel", "redhat",
    "centos", "rocky", "almalinux", "oracle", "oraclelinux", "linux", "suse", "template", "tmp",
    "temp", "test", "unknown", "none", "null", "n/a", "na", "-", "--", "host", "server", "vm",
    "new", "default", "node", "master", "worker", "esxi", "esx", "switch", "router", "firewall",
    "storage",
}
GENERIC_PATTERNS = [r"^\d{1,3}(\.\d{1,3}){3}$", r"^[\d\W_]+$", r"^template", r"^tmpl", r"localhost", r"\s"]
STOP_ENV = ("正式", "備援", "測試")
GUARD_ENV = ("正式", "備援", "測試")   # 護欄 2b：同名但環境不同 → 不併、待確認（2026-10-16 老賈）
STOP_TOTAL_PCT = 10
STOP_PENDING_PCT = 5
BUSINESS = ("asset_purpose", "api_id", "environment", "asset_status", "user_name",
            "custodian", "usage_unit", "big_ip_vip", "request_no")

_GEN_RE = [re.compile(p, re.I) for p in GENERIC_PATTERNS]


def strip_domain(h) -> str:
    h = (h or "").strip()
    m = DOMAIN_RE.match(h)
    return m.group(1) if m else h


def old_key(hostname, ip, serial) -> str:
    """現行口徑（＝system_stats.machine_key）。"""
    h = strip_domain(hostname).lower()
    i = (ip or "").strip()
    if h and i and i != "0.0.0.0":
        return f"{h}|{i}"
    return f"sn:{serial}"


def norm_name(h) -> str:
    return strip_domain((h or "").strip()).strip().lower()


def is_generic(n: str) -> bool:
    return (not n) or n in GENERIC_NAMES or any(p.search(n) for p in _GEN_RE)


_RAW_ENV = (("備援", ("備援", "災備", "dr")), ("正式", ("正式", "prod")),
            ("測試", ("測試", "uat", "dev", "test", "sit", "oa")))


def raw_env_class(v):
    t = str(v or "").strip().lower()
    if not t:
        return None
    for name, kws in _RAW_ENV:
        if any(k in t for k in kws):
            return name
    return None


def group_env(raw) -> str:
    v = (raw or "").strip()
    if not v:
        return ENV_EMPTY
    for name, kws in ENV_GROUPS:
        if any(k in v for k in kws):
            return name
    return ENV_FALLBACK


def _mac(v) -> str:
    return str(v or "").strip().lower().replace("-", ":").replace(".", ":")


def is_off_book(serial) -> bool:
    return bool(serial) and str(serial).startswith(OFF_BOOK_PREFIXES)


def analyse(rows: list[dict]) -> dict:
    """rows：hardware 的 dict（至少 asset_serial/hostname/ip/mac/hw_serial/environment/asset_status）。"""
    act = [r for r in rows if (r.get("asset_status") or "").strip() not in RETIRED_STATUS]
    byname: dict = collections.defaultdict(list)
    generic_rows = []
    for r in act:
        n = norm_name(r.get("hostname"))
        if (r.get("hostname") or "").strip() and is_generic(n):
            generic_rows.append(r)
        if n and not is_generic(n):
            byname[n].append(r)
    pending: dict = {}
    for n, g in byname.items():
        macs = {_mac(r.get("mac")) for r in g if _mac(r.get("mac"))}
        sns = {str(r.get("hw_serial")).strip().lower() for r in g if str(r.get("hw_serial") or "").strip()}
        why = []
        if len(macs) > 1:
            why.append(f"MAC {len(macs)} 種")
        if len(sns) > 1:
            why.append(f"硬體序號 {len(sns)} 種")
        es = {group_env(r.get("environment")) for r in g} & set(GUARD_ENV)
        if len(es) > 1:
            why.append("環境不同（" + "／".join(sorted(es)) + "）")
        if why:
            pending[n] = why

    def okey(r):
        return old_key(r.get("hostname"), r.get("ip"), r.get("asset_serial"))

    def nkey(r):
        n = norm_name(r.get("hostname"))
        if n and not is_generic(n) and n not in pending:
            return "h:" + n
        return okey(r)

    cover: dict = collections.defaultdict(set)
    envs: dict = collections.defaultdict(set)
    members: dict = collections.defaultdict(list)
    for r in act:
        k = nkey(r)
        cover[k].add(okey(r))
        members[k].append(r)
        e = group_env(r.get("environment"))
        if e in STOP_ENV:
            envs[k].add(e)
    old_n = len({okey(r) for r in act})
    new_n = len(cover)
    merged = sorted(k for k in cover if len(cover[k]) > 1)
    # 停損 a 用原始環境值獨立判（＝machine_identity.raw_env_class），不跟護欄同一套
    env_mixed = [k for k in merged
                 if len({c for c in (raw_env_class(m.get("environment")) for m in members[k]) if c}) > 1]
    merged_away = old_n - new_n
    pct = round((new_n - old_n) / old_n * 100, 2) if old_n else 0.0
    pend_pct = round(len(pending) / new_n * 100, 2) if new_n else 0.0
    stops = {
        "a": (len(env_mixed) > 0, f"不同環境被併成一台 {len(env_mixed)} 組"),
        "b": (abs(pct) > STOP_TOTAL_PCT, f"總台數 {old_n}→{new_n}（{pct}%），門檻 ±{STOP_TOTAL_PCT}%"),
        "c": (pend_pct > STOP_PENDING_PCT, f"待確認 {len(pending)} 組（{pend_pct}%），門檻 {STOP_PENDING_PCT}%"),
    }
    # 誤判表（2026-10-16）：A＝現行多算（同一個非通用主機名被現行規則拆成幾台，多出來的台數）；
    # B＝新口徑修掉的；C＝還剩的（＝待確認那些名字），按原因分。A＝B＋C 恆成立。
    # 沒主機名、通用名的列：無從判斷是不是同一台，不計入 A，另列。
    over = {n: len({okey(r) for r in g}) - 1 for n, g in byname.items()}
    A = sum(over.values())
    C_by: dict = collections.Counter()
    for n, why in pending.items():
        C_by[why[0].split()[0].split("（")[0]] += over[n]
    C = sum(C_by.values())
    B = A - C
    no_host = sum(1 for r in act if not (r.get("hostname") or "").strip())
    placeholder_ip = sum(1 for r in act if (r.get("hostname") or "").strip()
                         and (r.get("ip") or "").strip() in ("", "0.0.0.0"))
    misjudge = {"A": A, "B": B, "C": C, "C_by": dict(C_by),
                "D_pct": round(B / A * 100, 2) if A else 0.0,
                "no_hostname_rows": no_host, "generic_rows": len(generic_rows),
                "placeholder_ip_rows": placeholder_ip}
    return {
        "rows": len(rows), "active": len(act), "old": old_n, "new": new_n, "pct": pct,
        "merged": merged, "merged_away": merged_away, "env_mixed": env_mixed,
        "pending": pending, "generic_rows": generic_rows, "misjudge": misjudge,
        "stops": stops, "members": members, "cover": cover, "envs": envs,
        "breakdown": breakdown(act, okey, nkey),
        # 退役＝備查、不是資產（使用者 2026-10-17）：不計入、不合併；只報有幾筆跟現役同名
        "retired_rows": len(rows) - len(act),
        "retired_same_name": sum(1 for r in rows if (r.get("asset_status") or "").strip() in RETIRED_STATUS
                                 and norm_name(r.get("hostname")) in byname),
        "new_key": nkey, "old_key": okey,
    }


def mergeable_shells(rows: list[dict]) -> int:
    """Q77 掃描空殼可併回筆數（同 dyn_shell.classify 的 mergeable，用現行口徑）。"""
    live = [r for r in rows if (r.get("asset_status") or "").strip() not in RETIRED_STATUS]
    by_key: dict = collections.defaultdict(list)
    for r in live:
        by_key[old_key(r.get("hostname"), r.get("ip"), r.get("asset_serial"))].append(r)
    n = 0
    for r in live:
        if not str(r.get("asset_serial") or "").startswith("DYN-"):
            continue
        k = old_key(r.get("hostname"), r.get("ip"), None)
        if k.startswith("sn:"):
            continue
        m = _mac(r.get("mac"))
        twins, conflict = [], []
        for t in by_key.get(k, []):
            if is_off_book(t.get("asset_serial")):
                continue
            o = _mac(t.get("mac"))
            (conflict if (m and o and o != m) else twins).append(t)
        if not conflict and len(twins) == 1 and not any(str(r.get(c) or "").strip() for c in BUSINESS):
            n += 1
    return n


# ---- 台數拆分（只給使用者看，不上系統頁面）----
ENV_ORDER = ["正式", "測試", "備援", "未標環境"]
OS_ORDER = ["Linux", "Windows", "AIX", "其他", "未知"]
_LINUX = ("linux", "rhel", "red hat", "redhat", "centos", "ubuntu", "suse", "sles", "debian",
          "rocky", "alma", "oracle", "fedora", "amazon")


def os_class(v) -> str:
    """作業系統大類（關鍵字）。v 是 hardware.os——Q80 起它就是「實測優先、沒有才用 CIA」的顯示值。"""
    t = str(v or "").strip().lower()
    if not t or t in ("n/a", "na", "-", "none", "null", "unknown"):
        return "未知"
    if "aix" in t:
        return "AIX"
    if "windows" in t or t.startswith("win"):
        return "Windows"
    if any(k in t for k in _LINUX):
        return "Linux"
    return "其他"


def _machine_value(members: list[dict], fn, empty: str):
    """一台機器（一組登記）的值：照序號排，第一筆有值的為準（＝全站 collapse_machines 取代表列的規則）。
    回 (值, 是否有歧義＝成員之間值不只一種)。"""
    vals = [fn(m) for m in sorted(members, key=lambda m: str(m.get("asset_serial") or ""))]
    known = [v for v in vals if v != empty]
    return (known[0] if known else empty), len(set(known)) > 1


def _env_of(r) -> str:
    e = group_env(r.get("environment"))
    return e if e in ("正式", "測試", "備援") else "未標環境"


def breakdown(rows: list[dict], okey, nkey) -> dict:
    out = {}
    for name, fn, empty, order in (("env", _env_of, "未標環境", ENV_ORDER),
                                   ("os", lambda r: os_class(r.get("os")), "未知", OS_ORDER)):
        res = {}
        for side, kf in (("before", okey), ("after", nkey)):
            groups: dict = collections.defaultdict(list)
            for r in rows:
                groups[kf(r)].append(r)
            cnt = collections.Counter()
            amb = 0
            for g in groups.values():
                v, a = _machine_value(g, fn, empty)
                cnt[v] += 1
                amb += a
            res[side] = {k: cnt.get(k, 0) for k in order}
            res[side + "_total"] = len(groups)
            res[side + "_ambiguous"] = amb
        out[name] = res
    return out


def _print_breakdown(title: str, b: dict, order: list) -> None:
    print(f"── {title}（現行 → 新口徑）")
    for k in order:
        x, y = b["before"][k], b["after"][k]
        pct = f"{(y - x) / x * 100:+.2f}%" if x else "—"
        print(f"   {k:<6} {x:>6} → {y:<6} 差 {y - x:+d}（{pct}）")
    sb, sa = sum(b["before"].values()), sum(b["after"].values())
    ok = sb == b["before_total"] and sa == b["after_total"]
    print(f"   合計   {sb:>6} → {sa:<6}  {'✓ 等於總台數' if ok else '✗ 不等於總台數'}"
          f"（{b['before_total']}→{b['after_total']}）；值有歧義的台：現行 {b['before_ambiguous']}、"
          f"新口徑 {b['after_ambiguous']}（照序號第一筆有值的為準）")


# ---- 去識別化 ----
def mask_name(h) -> str:
    h = (h or "").strip()
    return (h[:3] + "***") if h else "（空）"


def mask_ip(ip) -> str:
    p = (ip or "").strip().split(".")
    return f"{p[0]}.{p[1]}.*.*" if len(p) == 4 else ("（空）" if not ip else "***")


def load(db_path: str) -> list[dict]:
    uri = "file:" + db_path.replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(hardware)")}
        want = ["asset_serial", "hostname", "ip", "mac", "hw_serial", "environment", "asset_status", "os",
                *[c for c in BUSINESS if c not in ("environment", "asset_status")]]
        sel = ", ".join(c if c in cols else f"NULL AS {c}" for c in want)
        return [dict(r) for r in conn.execute(f"SELECT {sel} FROM hardware")]
    finally:
        conn.close()


def main(argv: list[str]) -> int:
    db_path = argv[1] if len(argv) > 1 else os.environ.get("ASSET_DB_PATH", "/opt/webit3/data/asset.db")
    if not os.path.exists(db_path):
        print(f"找不到資料庫：{db_path}　用法：python3 {os.path.basename(argv[0])} <asset.db 路徑>")
        return 2
    rows = load(db_path)
    a = analyse(rows)
    print("台數口徑試算（Q79，唯讀）")
    print(f"登記 {a['rows']} 筆，非退役 {a['active']} 筆（退役 {a['retired_rows']} 筆＝備查，不計入、不合併；"
          f"其中 {a['retired_same_name']} 筆跟現役同名）")
    print(f"現行台數 {a['old']} → 新口徑 {a['new']}（減少 {a['old'] - a['new']} 台，{a['pct']}%）")
    print(f"自動合併 {len(a['merged'])} 組｜待確認 {len(a['pending'])} 組｜預設名排除 {len(a['generic_rows'])} 筆"
          f"｜Q77 掃描空殼可併回 {mergeable_shells(rows)} 筆")
    m = a["misjudge"]
    print(f"誤判表：A 現行多算 {m['A']} 台｜B 新口徑修掉 {m['B']}｜C 還剩 {m['C']} {m['C_by']}｜D＝B÷A {m['D_pct']}%")
    print(f"        不計入 A（無從判斷）：沒主機名 {m['no_hostname_rows']} 筆、通用名 {m['generic_rows']} 筆"
          f"；有主機名但 IP 空白／0.0.0.0 {m['placeholder_ip_rows']} 筆（新口徑可按名字判）")
    _print_breakdown("依環境", a["breakdown"]["env"], ENV_ORDER)
    _print_breakdown("依作業系統（hardware.os＝實測優先）", a["breakdown"]["os"], OS_ORDER)
    print("停損檢查：")
    hit = []
    for k, (bad, msg) in a["stops"].items():
        print(f"  {k} {'觸發' if bad else '未觸發'}：{msg}")
        if bad:
            hit.append(k)
    print("結論：" + (f"觸發停損 {'、'.join(hit)} → 不切換" if hit else "未觸發停損，可切換"))

    def show(title, keys, limit=5):
        print(f"── {title}（舉例 {min(limit, len(keys))}／{len(keys)}）")
        for k in keys[:limit]:
            ms = a["members"][k]
            print(f"   {mask_name(k[2:] if k.startswith('h:') else k)}：現行 {len(a['cover'][k])} 台 → 1 台；"
                  f"環境 {sorted(a['envs'][k]) or '—'}；IP {sorted({mask_ip(m.get('ip')) for m in ms})}")
    show("A 環境混併", a["env_mixed"])
    show("B 自動合併", sorted(a["merged"], key=lambda k: -len(a["cover"][k])))
    print(f"── D 待確認（舉例 {min(5, len(a['pending']))}／{len(a['pending'])}）")
    for n, why in list(a["pending"].items())[:5]:
        print(f"   {mask_name(n)}：{'、'.join(why)}")
    gen = collections.Counter(norm_name(r.get("hostname")) for r in a["generic_rows"])
    print(f"── C 預設名排除（前 5 種）：" + "、".join(f"{mask_name(n)}×{c}" for n, c in gen.most_common(5)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
