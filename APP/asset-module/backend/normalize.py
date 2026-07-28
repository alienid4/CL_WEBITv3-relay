"""髒資料正規化：把同一個東西的各種寫法收斂成標準名。

## 為什麼需要

同一份資料庫裡實際存在的（2026-07-19 真實資料）：
    Rocky Linux 9.7          ← Excel 匯入的
    Rocky Linux 9.7 (Blue Onyx)  ← facts 從機器上收的
    VMware VM                ← 人填的
    VMware Virtual Platform  ← DMI 讀出來的
不收斂就統計不出「我有幾台 Rocky 9.7」，因為它們是兩個不同的字串。

## 核心原則：原值永不改

正規化**不是**把原值改掉，是原值和標準值兩個都留：
    os              = "Rocky Linux 9.7 (Blue Onyx)"   ← 來源說什麼就是什麼，永不動
    os_canonical    = "Rocky Linux 9.7"               ← 算出來的標準名
哪天發現規則錯了，重跑就好；當初若直接覆蓋，原始資訊就永遠沒了。

## 兩層策略（單用一層都會壞）

1. **規則解析**：抓廠商＋產品＋版本。版本組合是無限的，字典列不完。
2. **別名字典**：規則橋不了的真實字差（Windows 11 → Microsoft Windows 11）。
   字典存 DB，人可以補，補完立刻生效。

查不到的**不亂猜**——標成 unmatched、原值原樣保留，並讓它出現在「待對應清單」，
人補一次就好。靜默猜錯比留白更糟：留白看得出來，猜錯看不出來。

## 為什麼不存成欄位（不做 normalized 欄位）

別名字典是會被人補的。存成欄位就會有「補了字典但舊資料還是舊的」的不同步問題，
而且要記得重跑。改成讀取時即時算：字典一補，全站立刻一致。
資料量到十萬筆再考慮加快取。
"""
from __future__ import annotations

import re

# ---- OS 規則：(比對樣式, 標準廠商, 標準產品名) ----
# 順序有意義：先具體後籠統（Windows Server 要排在 Windows 之前）。
_OS_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\bwindows\s+server\b", re.I), "Microsoft", "Windows Server"),
    (re.compile(r"\bwindows\b", re.I), "Microsoft", "Windows"),
    (re.compile(r"\brocky\s*linux\b", re.I), "Rocky Enterprise Software Foundation", "Rocky Linux"),
    (re.compile(r"\balma\s*linux\b", re.I), "AlmaLinux OS Foundation", "AlmaLinux"),
    (re.compile(r"\bubuntu\b", re.I), "Canonical", "Ubuntu"),
    (re.compile(r"\bdebian\b", re.I), "Debian Project", "Debian"),
    (re.compile(r"\b(red\s*hat|rhel)\b", re.I), "Red Hat", "Red Hat Enterprise Linux"),
    (re.compile(r"\bcentos\b", re.I), "CentOS Project", "CentOS"),
    (re.compile(r"\b(suse|sles)\b", re.I), "SUSE", "SUSE Linux Enterprise"),
    (re.compile(r"\bfedora\b", re.I), "Fedora Project", "Fedora"),
    (re.compile(r"\baix\b", re.I), "IBM", "AIX"),
    (re.compile(r"\bsolaris\b", re.I), "Oracle", "Solaris"),
    (re.compile(r"\bhp-?ux\b", re.I), "HPE", "HP-UX"),
]

# 版本號：抓第一組看起來像版本的數字（9.7 / 22.04 / 13 / 2022）
_VERSION = re.compile(r"\b(\d+(?:\.\d+)*)\b")

# 代號／發行名：Rocky 的 (Blue Onyx)、Debian 的 (trixie)、Ubuntu 的 LTS 字樣。
# 這些不影響「是哪個版本」，是造成同物異名的主因，正規化時要拿掉。
_CODENAME = re.compile(r"[\(（][^）)]*[\)）]")
# ⚠️ 只去「不影響版本判讀」的字。**不可以**把 linux 當雜訊拿掉——
# 產品名本身就含它（Rocky Linux），先拆掉再比對規則就永遠比不到（實際踩過）。
_NOISE = re.compile(r"\b(lts)\b", re.I)

# ---- 機型規則 ----
_MODEL_RULES: list[tuple[re.Pattern, str, str]] = [
    # VMware 的虛擬機在不同來源叫法不同：VMware VM / VMware Virtual Platform /
    # VMware7,1 …… 都是同一件事「這是一台 VMware 虛擬機」
    (re.compile(r"\bvmware\b", re.I), "VMware", "VMware Virtual Machine"),
    (re.compile(r"\b(kvm|qemu)\b", re.I), "QEMU", "KVM Virtual Machine"),
    (re.compile(r"\bvirtualbox\b", re.I), "Oracle", "VirtualBox VM"),
    (re.compile(r"\bhyper-?v\b|\bvirtual machine\b", re.I), "Microsoft", "Hyper-V Virtual Machine"),
    (re.compile(r"\bproliant\b", re.I), "HPE", "ProLiant"),
    (re.compile(r"\bpoweredge\b", re.I), "Dell", "PowerEdge"),
    (re.compile(r"\bthinksystem\b|\bsystem x\b", re.I), "Lenovo", "ThinkSystem"),
    (re.compile(r"\bsupermicro\b|\bsuper micro\b", re.I), "Supermicro", "Supermicro Server"),
]

KIND_OS = "os"
KIND_MODEL = "device_model"


def _clean(raw: str) -> str:
    s = _CODENAME.sub(" ", str(raw))       # 去掉 (Blue Onyx)／(trixie)
    s = _NOISE.sub(" ", s)                 # 去掉 LTS／GNU/Linux 這類不影響版本的字
    return re.sub(r"\s+", " ", s).strip()


def _load_aliases(conn, kind: str) -> dict[str, str]:
    """讀人工別名字典。查不到表就當空的——正規化不該因為字典還沒建就整個壞掉。"""
    try:
        return {
            str(r["raw_value"]).strip().lower(): r["canonical"]
            for r in conn.execute(
                "SELECT raw_value, canonical FROM normalize_alias WHERE kind = ?", (kind,)
            )
        }
    except Exception:  # noqa: BLE001 - 表不存在等情況
        return {}


def normalize_os(raw, conn=None) -> dict:
    """把 OS 字串收斂成標準名。回 {raw, canonical, vendor, product, version, matched, method}。

    matched=False 代表「認不出來」——canonical 會原樣回傳 raw，**不亂猜**。
    """
    if raw is None or str(raw).strip() == "":
        return {"raw": raw, "canonical": None, "vendor": None, "product": None,
                "version": None, "matched": False, "method": "empty"}

    text = str(raw).strip()

    # 別名字典優先：人工對應過的一定照人的意思
    if conn is not None:
        alias = _load_aliases(conn, KIND_OS).get(text.lower())
        if alias:
            return {"raw": raw, "canonical": alias, "vendor": None, "product": None,
                    "version": None, "matched": True, "method": "alias"}

    # 規則比對用**原字串**（產品名可能含被視為雜訊的字，如 Rocky Linux 的 linux）；
    # 版本抽取才用清理過的（去掉代號，否則會從 (trixie) 之類的括號內容誤抓數字）。
    cleaned = _clean(text)
    for pattern, vendor, product in _OS_RULES:
        if pattern.search(text):
            m = _VERSION.search(cleaned)
            version = m.group(1) if m else None
            canonical = f"{product} {version}".strip() if version else product
            return {"raw": raw, "canonical": canonical, "vendor": vendor,
                    "product": product, "version": version,
                    "matched": True, "method": "rule"}

    # 認不出來：原值原樣留著，標成未對應讓它出現在待處理清單
    return {"raw": raw, "canonical": text, "vendor": None, "product": None,
            "version": None, "matched": False, "method": "unmatched"}


def normalize_model(raw, conn=None) -> dict:
    """機型正規化。同一台 VMware 虛擬機在不同來源叫 VMware VM／VMware Virtual Platform，
    不收斂就會被統計成兩種機型。"""
    if raw is None or str(raw).strip() == "":
        return {"raw": raw, "canonical": None, "vendor": None,
                "matched": False, "method": "empty"}

    text = str(raw).strip()
    if conn is not None:
        alias = _load_aliases(conn, KIND_MODEL).get(text.lower())
        if alias:
            return {"raw": raw, "canonical": alias, "vendor": None,
                    "matched": True, "method": "alias"}

    for pattern, vendor, model in _MODEL_RULES:
        if pattern.search(text):
            return {"raw": raw, "canonical": model, "vendor": vendor,
                    "matched": True, "method": "rule"}

    return {"raw": raw, "canonical": text, "vendor": None,
            "matched": False, "method": "unmatched"}


def pending_values(conn) -> dict:
    """列出「規則和字典都認不出來」的原值＝待人工對應清單。

    刻意用即時查詢而不是另建一張 pending 表：pending 的定義就是
    「現在還認不出來的值」，另存一份就會有不同步的問題（字典補了、pending 沒清）。
    """
    out = {KIND_OS: [], KIND_MODEL: []}
    for kind, column, table, fn in (
        (KIND_OS, "os", "hardware", normalize_os),
        (KIND_MODEL, "device_model", "hardware", normalize_model),
    ):
        seen = {}
        for r in conn.execute(
            f"SELECT {column} AS v, COUNT(*) AS n FROM {table} "
            f"WHERE {column} IS NOT NULL AND {column} != '' GROUP BY {column}"
        ):
            res = fn(r["v"], conn)
            if not res["matched"]:
                seen[r["v"]] = r["n"]
        out[kind] = [{"raw_value": k, "count": v} for k, v in
                     sorted(seen.items(), key=lambda x: -x[1])]
    return out


# ---- 診斷外掛：這個功能出問題時，我需要看到的東西 ----
try:
    import diagnostics

    @diagnostics.register("normalize")
    def _diag(conn) -> dict:
        """給判斷過程，不給原始資料：每個原值走了哪條路、命中什麼、結果是什麼。"""
        trace = []
        for kind, column, fn in ((KIND_OS, "os", normalize_os),
                                 (KIND_MODEL, "device_model", normalize_model)):
            for r in conn.execute(
                f"SELECT {column} AS v, COUNT(*) AS n FROM hardware "
                f"WHERE {column} IS NOT NULL AND {column} != '' GROUP BY {column}"
            ):
                res = fn(r["v"], conn)
                trace.append({"kind": kind, "raw": r["v"], "count": r["n"],
                              "canonical": res["canonical"], "method": res["method"],
                              "matched": res["matched"]})
        aliases = [dict(r) for r in conn.execute(
            "SELECT kind, raw_value, canonical FROM normalize_alias")]
        return {
            "trace": trace,
            "alias_count": len(aliases),
            "aliases": aliases,
            "pending": pending_values(conn),
        }
except ImportError:  # 診斷模組不在時不影響正規化本身
    pass
