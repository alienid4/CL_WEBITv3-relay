"""主機履歷「CPU 分析」與「檔案系統空間」（第一期，2026-11-26）。

值班情境（使用者 2026-11-24）：收到某台 CPU 忙或磁碟 90% 的告警（或自己看到 80% 想先查），
在主機履歷按「檢測 ▾」，就看到「此刻是哪支程式／哪個掛載點、可不可以略過、找誰」，
再按「複製給群組」貼到 Teams 群組。

## 界線（危險區 D2：送到目標機執行的指令）
- **只讀**：/proc、ps、df、vmstat、lparstat、bindprocessor、WinRM 效能計數器——現有即時檢測已在用的同類查詢。
  不掃目錄（du，第二期）、不需 sudo、不裝任何東西。
- **不收程式的指令列參數**：參數裡常有密碼與連線字串。只收程式名、PID、帳號、CPU、systemd unit／服務名。
- 指令是常數，**沒有任何使用者輸入進到指令裡**；IP 只當連線目標。
- 平台指令前面加 VMware 自我檢查（同即時檢測），ESXi／vCenter 連上後立刻離開。
- 讀 /proc 用 shell 內建 read，不展開成一長串參數（大量程式的主機不會 Argument list too long），
  每行加前綴 `P `，程式名就算含換行也偽造不了段落標記或整機那一行（Antigravity C2／C3）。
- df 只看本機檔案系統（-l）並包 timeout，失聯的 NFS 不會把整次分析卡住（C8／D13）。

## CPU 怎麼算
- Linux：/proc/stat 與 /proc/[pid]/stat 取三次（間隔約 1 秒），tick 差值、整機為分母，PID＋starttime 對齊；
  前後兩段各算一次整機值，變化大時明講（D09）。user／sys 分開保留（D02）。
- AIX：/proc 是二進位，用 ps 累積 CPU 秒數兩次相減（秒級粗估）；整機看 vmstat（丟掉第一筆開機平均，C12）。
  共用 LPAR 有 pc（實際用掉幾顆實體核）時，「占已用 %」以 pc 為分母（D06）。
- Windows：ProcessorInformation（支援 >64 核）＋PerfProc_Process 兩次取樣；服務名用 Win32_Service 的 ProcessId 對應（D07）。
- 結論一律標「此刻快照」，不寫「持續」。
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
import threading

RULE_VERSION = "host_diag-2"
KEEP_DAYS = 90                 # 分析結果保留天數；誰在何時對哪台做了什麼另寫 collect_log
COOLDOWN_SEC = 30              # 同台同種「成功」分析後的冷卻（D17：失敗不冷卻、縮短成 30 秒）
RUNNING_STALE_SEC = 300        # 「執行中」超過這麼久＝中斷（服務重啟留下的），不再擋人
MAX_CONCURRENT = 5             # 全站同時在跑的分析上限（D16）
USER_PER_MIN = 10              # 同一個帳號每分鐘最多幾次（D16）
TOP_N = 10

# 分類規則（依序判斷；畫面「依據」直接顯示這幾個門檻）
STEAL_PCT = 10.0               # steal ≥ → 虛擬化主機在搶 CPU
IOWAIT_PCT = 30.0              # iowait ≥ → 等磁碟（程式也很忙時改判「運算＋等磁碟」）
IOWAIT_IDLE_PCT = 20.0         # 整機不忙時，iowait ≥ 這個也算等磁碟（不可以判不忙）
BUSY_PCT = 50.0                # 程式＋系統 < → 不忙（但先檢查單核滿載、排隊）
CORE_FULL = 0.8                # 單一程式 0.8～1.2 顆核心 → 單核滿載（單執行緒卡住的樣子）
CORE_SINGLE_MAX = 1.2          # 超過＝多執行緒在分工，不算單核滿載
LOAD_PER_CPU = 1.0             # load1 ÷ 核數 ≥ → 工作在排隊
SYS_PCT = 30.0                 # 系統時間 ≥ → 核心
SYS_BY_ONE = 0.5               # 單一程式的系統時間占全部系統時間 ≥ → 指名是它觸發
UNATTRIB_SHARE = 0.30          # 找不到常駐程式的 CPU ≥ 已用的這個比例 → 不指名小程式
SINGLE_SHARE = 0.60            # 同一程式（同名同帳號合計）≥ 已用的這個比例 → 指名
SWING_PP = 25.0                # 前後兩段整機差 ≥ → 負載劇烈波動

KINDS = ("cpu", "fs")
KIND_LABEL = {"cpu": "CPU 分析", "fs": "檔案系統空間"}
PLATFORMS = ("linux", "aix", "windows")

_DF_X = "-x tmpfs -x devtmpfs -x overlay -x squashfs -x aufs -x iso9660 -x udf"

LINUX_CPU_CMD = (
    "export LC_ALL=C; "
    "echo @@NCPU; nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null; "
    "echo @@HZ; getconf CLK_TCK 2>/dev/null; "
    "echo @@LOAD; cat /proc/loadavg 2>/dev/null; "
    "echo @@HIDEPID; grep -E '^[^ ]+ /proc proc .*hidepid' /proc/mounts 2>/dev/null; "
    "snap() { echo \"@@$1\"; read -r c 2>/dev/null < /proc/stat && echo \"$c\"; "
    "read -r u 2>/dev/null < /proc/uptime && echo \"U $u\"; "
    "for f in /proc/[0-9]*/stat; do read -r l 2>/dev/null < \"$f\" && echo \"P $l\"; done; }; "
    "snap S0; sleep 1; snap S1; sleep 1; snap S2; "
    "echo @@USERS; ps -e -o pid= -o user:32= 2>/dev/null; "
    "echo @@CG; for f in /proc/[0-9]*/cgroup; do u=; "
    "while read -r l; do case \"$l\" in *.service*|*.scope*|*kubepods*|*docker*|*libpod*) u=\"$l\";; esac; done 2>/dev/null < \"$f\"; "
    "[ -n \"$u\" ] && { p=${f#/proc/}; echo \"C ${p%/cgroup} ${u##*/}\"; }; done; "
    "echo @@END"
)

AIX_PS = "ps -e -o pid= -o user= -o time= -o etime= -o comm= 2>/dev/null"
AIX_CPU_CMD = (
    "export LC_ALL=C; "
    "echo @@NCPU; bindprocessor -q 2>/dev/null; "
    "echo @@LPAR; lparstat -i 2>/dev/null | grep -E 'Online Virtual CPUs|Entitled Capacity|Type'; "
    "echo @@T0; date +%H:%M:%S; "
    "echo @@P0; " + AIX_PS + "; "
    "echo @@VMSTAT; vmstat 1 3 2>/dev/null; "
    "echo @@T1; date +%H:%M:%S; "
    "echo @@P1; " + AIX_PS + "; "
    "echo @@END"
)

WIN_CPU_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
"@@NCPU"
(Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
"@@SVC"
Get-CimInstance Win32_Service -Filter "State='Running'" | ForEach-Object { "V|$($_.ProcessId)|$($_.Name)" }
Get-CimInstance Win32_PerfRawData_W3SVCW3WPCounters_W3SVCW3WP | ForEach-Object { "A|$($_.Name)" }
function Snap($t) {
  "@@$t"
  $c = Get-CimInstance Win32_PerfRawData_Counters_ProcessorInformation | Where-Object { $_.Name -notlike '*_Total*' }
  if (-not $c) { $c = Get-CimInstance Win32_PerfRawData_PerfOS_Processor | Where-Object { $_.Name -ne '_Total' } }
  $c | ForEach-Object { "C|$($_.Name)|$($_.PercentProcessorTime)|$($_.PercentPrivilegedTime)|$($_.Timestamp_Sys100NS)" }
  Get-CimInstance Win32_PerfRawData_PerfProc_Process | Where-Object { $_.Name -ne '_Total' -and $_.Name -ne 'Idle' } | ForEach-Object {
    "P|$($_.IDProcess)|$($_.ElapsedTime)|$($_.PercentProcessorTime)|$($_.PercentPrivilegedTime)|$($_.Timestamp_Sys100NS)|$($_.Timestamp_Object)|$($_.Frequency_Object)|$($_.Name)"
  }
}
Snap 'S0'; Start-Sleep -Seconds 2; Snap 'S1'
"@@END"
"""

LINUX_FS_CMD = (
    "export LC_ALL=C; T=; command -v timeout >/dev/null 2>&1 && T='timeout 10'; "
    "echo @@DF; $T df -P -k -l " + _DF_X + " 2>/dev/null; "
    "echo @@DFI; $T df -P -i -l " + _DF_X + " 2>/dev/null; "
    "echo @@MOUNTS; cat /proc/mounts 2>/dev/null; echo @@END"
)
# AIX 沒有 timeout 指令：只對本機 jfs／jfs2 的掛載點逐一 df，失聯的 NFS 不會被碰到、不會卡住（審查 M2）。
# AIX `mount` 本機列沒有 node 欄：$1 裝置、$2 掛載點、$3 檔案系統種類；NFS 列多一欄 node，$3 不會是 jfs。【未實機驗證，等 AIX 實測】
AIX_FS_CMD = ("export LC_ALL=C; echo @@DF; "
              "for m in $(mount 2>/dev/null | awk '$3 ~ /^jfs/ {print $2}'); do df -k \"$m\" 2>/dev/null; done; echo @@END")
WIN_FS_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
"@@VOL"
Get-CimInstance Win32_Volume -Filter "DriveType=3" | Where-Object { $_.Name -and $_.Name -notlike '\\?\*' -and $_.Capacity -gt 0 } | ForEach-Object { "$($_.Name)|$($_.Capacity)|$($_.FreeSpace)" }
"@@END"
"""


class NotProbed(Exception):
    """我們這邊的問題（沒有金鑰／沒有憑證／平台不支援）：不是那台主機的狀態，不給「複製給群組」。"""


class Busy(Exception):
    def __init__(self, msg: str, latest_id: int | None = None):
        super().__init__(msg)
        self.latest_id = latest_id


_CTRL = re.compile(r"[\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")


def clean(s, n: int = 64) -> str:
    """目標機回來的程式名／掛載點：去控制字元與方向控制字元、截長度；開頭是 = + - @ 的加單引號（匯出 Excel 不變公式）。"""
    s = _CTRL.sub(" ", str(s or "")).strip()[:n]
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


def _split(out: str) -> dict:
    import health_probe
    return health_probe._split_markers(out or "")


def _first_int(text: str, default: int = 0) -> int:
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else default


def _r1(v) -> float | None:
    return None if v is None else round(float(v), 1)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


# ======================================================================= Linux
def parse_proc_snapshot(text: str) -> tuple[list[int] | None, float | None, dict]:
    """一段取樣 → (整機 cpu 列, uptime 秒, {(pid, starttime): (comm, utime, stime)})。
    整機只認**第一個** `cpu ` 行；程式行一定是 `P ` 開頭（程式名偽造不了）。comm 從最後一個 ')' 切。"""
    cpu, up, procs = None, None, {}
    for line in (text or "").splitlines():
        if line.startswith("cpu ") and cpu is None:
            try:
                cpu = [int(x) for x in line.split()[1:]]
            except ValueError:
                cpu = None
            continue
        if line.startswith("U ") and up is None:
            try:
                up = float(line.split()[1])
            except (IndexError, ValueError):
                pass
            continue
        if not line.startswith("P "):
            continue
        body = line[2:]
        lp, rp = body.find("("), body.rfind(")")
        if lp <= 0 or rp < lp:
            continue
        pid_s, comm, rest = body[:lp].strip(), body[lp + 1:rp], body[rp + 1:].split()
        if not pid_s.isdigit() or len(rest) < 20:
            continue
        try:
            procs[(int(pid_s), int(rest[19]))] = (comm, int(rest[11]), int(rest[12]))
        except ValueError:
            continue
    return cpu, up, procs


def _kv_lines(text: str, prefix: str) -> dict:
    out = {}
    for line in (text or "").splitlines():
        p = line.split(None, 2)
        if len(p) == 3 and p[0] == prefix and p[1].isdigit():
            out[int(p[1])] = p[2].strip()
    return out


def _users(text: str) -> dict:
    out = {}
    for line in (text or "").splitlines():
        p = line.split()
        if len(p) >= 2 and p[0].isdigit():
            out[int(p[0])] = p[1]
    return out


def _cpu_phase(c0: list[int], c1: list[int]) -> dict | None:
    d = [max(0, b - a) for a, b in zip(c0, c1)]          # 虛擬機 iowait／idle 偶爾倒退：夾成 0（C15）
    total = sum(d)
    if total <= 0:
        return None
    g = lambda i: d[i] if len(d) > i else 0               # noqa: E731 user nice system idle iowait irq softirq steal
    return {"total": total, "user": g(0) + g(1), "sys": g(2) + g(5) + g(6), "idle": g(3), "iowait": g(4), "steal": g(7)}


def analyze_linux_cpu(out: str) -> dict:
    seg = _split(out)
    ncpu = _first_int(seg.get("NCPU", ""), 1) or 1
    hz = _first_int(seg.get("HZ", ""), 100) or 100
    snaps = [parse_proc_snapshot(seg.get(k, "")) for k in ("S0", "S1", "S2")]
    snaps = [s for s in snaps if s[0]]
    if len(snaps) < 2:
        return _no_data("讀不到 /proc/stat（不是 Linux，或指令被擋）")
    (c0, _u0, p0), (c1, up1, p1) = snaps[0], snaps[-1]
    ph = _cpu_phase(c0, c1)
    if not ph:
        return _no_data("兩次取樣之間 CPU 計數沒有變化")
    T = ph["total"]
    res = {"platform": "linux", "ncpu": ncpu, "window_sec": _r1(T / (ncpu * hz)), "precision": "精確（tick 差值）",
           "busy_pct": _r1(100 * (ph["user"] + ph["sys"]) / T), "user_pct": _r1(100 * ph["user"] / T),
           "sys_pct": _r1(100 * ph["sys"] / T), "iowait_pct": _r1(100 * ph["iowait"] / T),
           "steal_pct": _r1(100 * ph["steal"] / T), "load": _load(seg.get("LOAD", ""))}
    res["total_pct"] = _r1(res["busy_pct"] + res["steal_pct"])
    if len(snaps) == 3:
        a, b = _cpu_phase(snaps[0][0], snaps[1][0]), _cpu_phase(snaps[1][0], snaps[2][0])
        if a and b:
            res["phases"] = [_r1(100 * (a["user"] + a["sys"]) / a["total"]), _r1(100 * (b["user"] + b["sys"]) / b["total"])]
    if (seg.get("HIDEPID") or "").strip():
        res["hidepid"] = True
    users, units = _users(seg.get("USERS", "")), _kv_lines(seg.get("CG", ""), "C")
    procs = []
    for key, (comm, u1, s1) in p1.items():
        if key not in p0:                     # 取樣期間才出現的程式（或 PID 被重用）：不算差值，算進「未能歸屬」
            continue
        _c, u0, s0 = p0[key]
        du, ds = max(0, u1 - u0), max(0, s1 - s0)
        if du + ds <= 0:
            continue
        pid, start = key
        age = round((up1 - start / hz) / 86400, 1) if up1 is not None else None
        procs.append({"pid": pid, "name": clean(comm), "user": clean(users.get(pid) or "", 32),
                      "unit": clean(units.get(pid) or "", 80), "raw": 100 * (du + ds) / T, "sys_raw": 100 * ds / T,
                      "age_days": age if age is not None and age >= 0 else None})
    return _finish(res, procs)


def _load(text: str) -> list[float] | None:
    p = (text or "").split()
    try:
        return [float(p[0]), float(p[1]), float(p[2])]
    except (IndexError, ValueError):
        return None


# ========================================================================= AIX
def _ps_time(s: str) -> int | None:
    """AIX ps 的時間欄：[[dd-]hh:]mm:ss → 秒。"""
    s = (s or "").strip()
    days = 0
    if "-" in s:
        dd, s = s.split("-", 1)
        if not dd.isdigit():
            return None
        days = int(dd)
    parts = s.split(":")
    if not all(x.isdigit() for x in parts) or not 1 <= len(parts) <= 3:
        return None
    secs = 0
    for x in parts:
        secs = secs * 60 + int(x)
    return days * 86400 + secs


def _aix_ps(text: str) -> dict:
    out = {}
    for line in (text or "").splitlines():
        p = line.split(None, 4)
        if len(p) < 5 or not p[0].isdigit():
            continue
        t, et = _ps_time(p[2]), _ps_time(p[3])
        if t is None:
            continue
        out[(int(p[0]), p[4].strip())] = (p[1], t, et)
    return out


def parse_aix_vmstat_cpu(text: str) -> dict | None:
    """vmstat 的 us／sy／id／wa（＋共用 LPAR 的 pc／ec）。依表頭找欄位（共用 LPAR wa 後面還有 pc、ec）；
    **丟掉第一筆**（開機以來平均），其餘取平均（C12）。只有一筆就回 None，不拿開機平均冒充此刻。"""
    head, rows = None, []
    for ln in (text or "").splitlines():
        p = ln.split()
        if {"us", "sy", "id", "wa"} <= set(p):
            head = p
            continue
        if head and len(p) == len(head):
            try:
                rows.append([float(x) for x in p])
            except ValueError:
                continue
    if not head or len(rows) < 2:
        return None
    rows = rows[1:]
    avg = [sum(c) / len(rows) for c in zip(*rows)]
    v = dict(zip(head, avg))
    out = {"us": v["us"], "sy": v["sy"], "id": v["id"], "wa": v["wa"]}
    if "pc" in v:
        out["pc"], out["ec"] = v["pc"], v.get("ec")
    return out


def _hms(text: str) -> int | None:
    m = re.search(r"(\d{1,2}):(\d{2}):(\d{2})", text or "")
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) if m else None


def analyze_aix_cpu(out: str) -> dict:
    seg = _split(out)
    # bindprocessor -q：「The available processors are:  0 1 2 3」→ 冒號後面有幾個數字就是幾顆邏輯 CPU
    nc = seg.get("NCPU") or ""
    ncpu = (len(re.findall(r"\d+", nc.split(":", 1)[1])) if ":" in nc else 0) or 1
    vm = parse_aix_vmstat_cpu(seg.get("VMSTAT", ""))
    p0, p1 = _aix_ps(seg.get("P0", "")), _aix_ps(seg.get("P1", ""))
    t0, t1 = _hms(seg.get("T0", "")), _hms(seg.get("T1", ""))
    win = float((t1 - t0) % 86400) if t0 is not None and t1 is not None else 0.0
    win = win if 1 <= win <= 30 else 2.0          # 時鐘回撥或讀不到：用 vmstat 的取樣長度（C12）
    if not vm and not p1:
        return _no_data("讀不到 vmstat 與 ps（不是 AIX，或指令被擋）")
    shared = "Shared" in (seg.get("LPAR") or "")
    res = {"platform": "aix", "ncpu": ncpu, "window_sec": win, "precision": "粗估（AIX 程式 CPU 時間只到秒）",
           "load": None, "steal_pct": None}
    if vm:
        res.update({"busy_pct": _r1(vm["us"] + vm["sy"]), "user_pct": _r1(vm["us"]), "sys_pct": _r1(vm["sy"]),
                    "iowait_pct": _r1(vm["wa"])})
        if vm.get("pc") is not None:
            res["pc"], res["ec"] = _r1(vm["pc"]), _r1(vm.get("ec"))
    else:
        res.update({"busy_pct": None, "user_pct": None, "sys_pct": None, "iowait_pct": None})
    res["total_pct"] = res["busy_pct"]
    if shared:
        res["note"] = "共用 LPAR：整機 % 是相對分配到的算力；「占已用 %」以實際用掉的實體核（pc）為準"
    procs = []
    for key, (user, t, et) in p1.items():
        if key not in p0:
            continue
        _u, t_0, et0 = p0[key]
        if et is not None and et0 is not None and t0 is not None and t1 is not None \
                and abs((t1 - et) - (t0 - et0)) > 3:      # 啟動時間對不上＝同名程式重用了 PID（C20）
            continue
        dt = t - t_0
        if dt <= 0:
            continue
        cores = dt / win
        procs.append({"pid": key[0], "name": clean(key[1]), "user": clean(user, 32), "unit": "",
                      "raw": 100 * cores / ncpu, "sys_raw": None, "cores": cores,
                      "age_days": round(et / 86400, 1) if et is not None else None})
    return _finish(res, procs)


# ===================================================================== Windows
def analyze_win_cpu(out: str) -> dict:
    seg = _split(out)
    n_cs = _first_int(seg.get("NCPU", ""), 0)
    svc: dict = {}
    for line in (seg.get("SVC") or "").splitlines():
        p = line.strip().split("|")
        if len(p) == 3 and p[0] == "V" and p[1].isdigit() and p[1] != "0":
            svc.setdefault(int(p[1]), []).append(p[2])
        # IIS 的 w3wp：計數器實例名是「PID_應用程式集區」，不用讀指令列就知道是哪個 AppPool（裁判題 4）
        m = re.match(r"^A\|(\d+)_(.+)$", line.strip())
        if m and not m[2].startswith("_Total"):
            svc.setdefault(int(m[1]), []).append(f"AppPool {m[2]}")
    snaps = []
    for k in ("S0", "S1", "S2"):
        if k not in seg:
            continue
        cpus, procs = {}, {}
        for line in seg[k].splitlines():
            p = line.strip().split("|")
            try:
                if p[0] == "C" and len(p) == 5:
                    cpus[p[1]] = (int(p[2]), int(p[3]), int(p[4]))
                elif p[0] == "P" and len(p) >= 9:
                    procs[(int(p[1]), int(p[2]))] = ("|".join(p[8:]), int(p[3]), int(p[4]), int(p[5]), int(p[6]), int(p[7]))
            except (ValueError, IndexError):
                continue
        if cpus:
            snaps.append((cpus, procs))
    if len(snaps) < 2:
        return _no_data("讀不到 Windows 效能計數器")
    (c0, p0), (c1, p1) = snaps[0], snaps[-1]
    busy = priv = 0.0
    n, win = 0, 0.0
    for name, (idle1, pr1, ts1) in c1.items():
        if name not in c0:
            continue
        idle0, pr0, ts0 = c0[name]
        dts = ts1 - ts0
        if dts <= 0:
            continue
        busy += _clamp(1 - (idle1 - idle0) / dts, 0, 1)
        priv += _clamp((pr1 - pr0) / dts, 0, 1)
        n += 1
        win = max(win, dts / 1e7)
    if not n:
        return _no_data("Windows 效能計數器兩次取樣沒有變化")
    ncpu = max(n_cs, n)                                  # 計數器與 CIM 取大的，避免分母取到 1（C11）
    total, sys_ = 100 * busy / n, 100 * priv / n
    res = {"platform": "windows", "ncpu": ncpu, "window_sec": _r1(win), "precision": "精確（效能計數器差值）",
           "busy_pct": _r1(total), "total_pct": _r1(total), "user_pct": _r1(max(0.0, total - sys_)), "sys_pct": _r1(sys_),
           "iowait_pct": None, "steal_pct": None, "load": None,
           "note": "Windows 沒有 load／iowait；程式帳號要管理員權限才看得到，不收"}
    if not p1:
        res["no_proc_counter"] = True
    procs = []
    for key, (name, t1, s1, ts1, tobj, freq) in p1.items():
        if key not in p0:
            continue
        _n0, t0, s0, ts0, _o, _f = p0[key]
        dts = ts1 - ts0
        if dts <= 0 or t1 <= t0:
            continue
        age = (tobj - key[1]) / freq / 86400 if freq > 0 and key[1] > 0 and tobj >= key[1] else None
        procs.append({"pid": key[0], "name": clean(re.sub(r"#\d+$", "", name)), "user": "",
                      "unit": clean("、".join(svc.get(key[0], [])[:3]), 80),
                      "raw": 100 * (t1 - t0) / (dts * ncpu), "sys_raw": 100 * max(0, s1 - s0) / (dts * ncpu),
                      "age_days": round(age, 1) if age is not None and age < 36500 else None})
    return _finish(res, procs)


# ======================================================================= 共用
def _no_data(why: str) -> dict:
    return {"ok": False, "error": why, "procs": [], "groups": [], "verdict": "unknown", "verdict_text": f"分析不出來：{why}",
            "advice": "請改用完整健檢看整體，或登入主機確認"}


def _groups(procs: list[dict]) -> list[dict]:
    """同程式名＋同帳號合計（多行程服務：oracle、httpd、php-fpm…，D03）。"""
    g: dict = {}
    for p in procs:
        k = (p["name"], p.get("user") or "")
        x = g.setdefault(k, {"name": p["name"], "user": p.get("user") or "", "raw": 0.0, "count": 0, "unit": p.get("unit") or "",
                             "cores": 0.0, "sys_raw": 0.0})
        x["raw"] += p["raw"]
        x["count"] += 1
        x["cores"] += p.get("cores") or 0.0
        x["sys_raw"] += p.get("sys_raw") or 0.0
        x["unit"] = x["unit"] or p.get("unit") or ""
    return sorted(g.values(), key=lambda x: -x["raw"])


def _who(x: dict) -> str:
    bits = []
    if x.get("pid"):
        bits.append(f"PID {x['pid']}")
    if x.get("count", 1) > 1:
        bits.append(f"共 {x['count']} 個行程")
    if x.get("user"):
        bits.append(f"帳號 {x['user']}")
    if x.get("unit"):
        bits.append(f"服務 {x['unit']}")
    return f"{x['name']}（{'，'.join(bits)}）" if bits else x["name"]


def classify(res: dict, procs: list[dict], groups: list[dict]) -> tuple[str, str, str]:
    """回 (代碼, 判讀, 建議)。規則依序判斷，畫面「依據」照這裡寫（Antigravity D01～D05）。"""
    busy, io, sys_ = res.get("busy_pct"), res.get("iowait_pct"), res.get("sys_pct")
    steal, ncpu = res.get("steal_pct"), res.get("ncpu") or 1
    top_g = groups[0] if groups else None
    top_share = res.get("_top_share")
    if busy is None:
        return "unknown", "整機用量讀不到，無法判讀", "請用完整健檢或登入主機確認"
    if steal is not None and steal >= STEAL_PCT:
        return ("steal", f"虛擬化主機在搶 CPU（steal {steal:g}%）：這台分不到算力，不一定是自己的程式",
                "請 SP／虛擬化管理者看承載的 ESXi 負載與 CPU Ready")
    if io is not None and io >= IOWAIT_PCT and busy >= BUSY_PCT:
        who = f"；最多的是 {_who(top_g)}" if top_g else ""
        return ("io_and_cpu", f"程式在運算、同時大量等磁碟（程式＋系統 {busy:g}%、等磁碟 {io:g}%）{who}",
                "請 AP 確認這支程式的讀寫量，SP 同時看磁碟／儲存效能")
    if io is not None and (io >= IOWAIT_PCT or (busy < BUSY_PCT and io >= IOWAIT_IDLE_PCT)):
        return ("io_wait", f"CPU 大多在等磁碟（等磁碟 {io:g}%），不是程式在吃 CPU",
                "請 SP 看這台的磁碟／儲存效能")
    unatt = res.get("unattributed_pct")
    if unatt is not None and busy >= 20 and unatt >= UNATTRIB_SHARE * busy:
        if res.get("hidepid"):
            return ("unattributed", f"約 {unatt:g}% 的 CPU 看不到是哪支程式：這台的 /proc 有 hidepid，收集帳號看不到別人的程式",
                    "請有權限的人登入用 top 看；這不是「沒有程式在用」")
        return ("unattributed", f"約 {unatt:g}% 的 CPU 找不到常駐程式（常見於短命程式大量建立、程式不斷重啟、或權限看不到）",
                "請 AP 確認排程或程式是否不斷重啟；稍後再分析一次比對")
    if busy < BUSY_PCT:
        top = procs[0] if procs else None
        # 只在「剛好卡在一顆核心」時才說單核滿載：多執行緒程式用到 2 顆以上是正常分工，不是卡住
        # （使用者 11-26 實機 top：kafka 的 java 三個各 200%、整機 32%＝不忙）
        top_cores = top.get("cores", top["raw"] * ncpu / 100) if top else 0
        if top and CORE_FULL <= top_cores <= CORE_SINGLE_MAX and ncpu > 1:
            return ("single_core", f"單一程式吃滿一顆核心：{_who(top)} 此刻 {top['pct']:g}%（整機才 {busy:g}%）",
                    "單執行緒程式可能卡住或滿載，請 AP 確認")
        ld = res.get("load")
        if ld and ld[0] / ncpu >= LOAD_PER_CPU:
            return ("queue", f"整機 {busy:g}% 不高，但工作在排隊（1 分鐘負載 {ld[0]:g}、{ncpu} 核）",
                    "常見於等磁碟或鎖；請 SP 確認")
        most = f"；用最多的是 {_who(top_g)} 合計 {top_g['pct']:g}%" if top_g else ""
        return ("idle", f"此刻 CPU 不忙（程式＋系統 {busy:g}%）{most}",
                "不代表告警是誤報：請先對照告警時間與監控趨勢，必要時稍後再分析一次")
    if sys_ is not None and sys_ >= SYS_PCT:
        by = max(procs, key=lambda p: p.get("sys_raw") or 0, default=None)
        if by and by.get("sys_raw") and by["sys_raw"] >= SYS_BY_ONE * sys_:
            return ("kernel_by", f"系統（核心）時間偏高（{sys_:g}%），主要由 {_who(by)} 觸發大量系統呼叫",
                    "請 AP 與 SP 一起確認這支程式（例如大量寫 log、反覆開關連線）")
        return ("kernel", f"系統（核心）時間偏高（{sys_:g}%），沒有單一程式主導",
                "常見於大量 I/O、網路或記憶體不足換頁；請 SP 確認")
    if top_g and top_share is not None and top_share >= SINGLE_SHARE:
        if top_g["count"] > 1:
            return ("service", f"同一支程式的多個行程合計占大部分：{_who(top_g)} 合計此刻 {top_g['pct']:g}%",
                    "請這個服務的 AP 確認是否為尖峰或異常")
        return ("single", f"單一程式占大部分：{_who(top_g)} 此刻 {top_g['pct']:g}%",
                "請這支程式的 AP 確認是否正常（批次、迴圈、卡住）")
    names = "、".join(f"{g['name']} {g['pct']:g}%" for g in groups[:3])
    return "multi", f"多支程式一起用：{names}", "整體負載高，請 AP 確認是否為尖峰時段或排程重疊"


def _finish(res: dict, procs: list[dict]) -> dict:
    """先用原始值算占比與判讀，最後才四捨五入（C16）。"""
    busy = res.get("busy_pct")
    pc = res.get("pc")
    procs.sort(key=lambda p: -p["raw"])
    for p in procs:
        p["pct"] = _r1(_clamp(p["raw"]))
        if pc:
            p["share"] = (p.get("cores") or 0) / pc if pc > 0 else None
        else:
            p["share"] = p["raw"] / busy if busy else None
    groups = _groups(procs)
    for g in groups:
        g["pct"] = _r1(_clamp(g["raw"]))
        g["share"] = (g["cores"] / pc if pc else g["raw"] / busy if busy else None)
    res["_top_share"] = groups[0]["share"] if groups and groups[0]["share"] is not None else None
    if busy is not None and not pc:
        res["unattributed_pct"] = _r1(max(0.0, busy - sum(p["raw"] for p in procs)))
    code, text, advice = classify(res, procs, groups)
    sw = res.get("phases")
    if sw and abs(sw[0] - sw[1]) >= SWING_PP:
        text += f"（負載劇烈波動：前段 {sw[0]:g}% → 後段 {sw[1]:g}%）"
    if any(re.search(r"kubepods|docker|libpod|containerd", p.get("unit") or "") for p in procs[:TOP_N]):
        res["note"] = ((res.get("note") + "；") if res.get("note") else "") + \
            "這台有容器：此分析是整台主機的角度，不含單一容器被配額限流的情形"
    if res.get("no_proc_counter"):
        text += "；讀不到程式的效能計數器（權限或計數器停用），程式清單是空的"
    top = procs[:TOP_N]
    for i, p in enumerate(top, 1):
        p["rank"] = i
        p["share_pct"] = _r1(_clamp(100 * p["share"])) if p.get("share") is not None else None
        for k in ("raw", "sys_raw", "share", "cores"):
            p.pop(k, None)
    gtop = groups[:TOP_N]
    for i, g in enumerate(gtop, 1):
        g["rank"] = i
        g["share_pct"] = _r1(_clamp(100 * g["share"])) if g.get("share") is not None else None
        for k in ("raw", "sys_raw", "share", "cores"):
            g.pop(k, None)
    res.pop("_top_share", None)
    res.update({"ok": True, "error": None, "procs": top, "groups": gtop, "proc_count": len(procs),
                "verdict": code, "verdict_text": text, "advice": advice,
                "rules": {"steal_pct": STEAL_PCT, "iowait_pct": IOWAIT_PCT, "iowait_idle_pct": IOWAIT_IDLE_PCT,
                          "busy_pct": BUSY_PCT, "core_full": CORE_FULL, "core_single_max": CORE_SINGLE_MAX, "load_per_cpu": LOAD_PER_CPU, "sys_pct": SYS_PCT,
                          "unattrib_share": UNATTRIB_SHARE, "single_share": SINGLE_SHARE, "swing_pp": SWING_PP}})
    return res


# ================================================================= 檔案系統空間
def _parse_win_vol(text: str) -> list[dict]:
    out = []
    for line in (text or "").splitlines():
        p = line.strip().rsplit("|", 2)
        if len(p) != 3:
            continue
        try:
            cap, free = float(p[1]), float(p[2])
        except ValueError:
            continue
        if cap <= 0:
            continue
        out.append({"mount": p[0], "fs": "NTFS", "use_pct": round((cap - free) / cap * 100, 1), "inode_pct": None,
                    "size_kb": int(cap / 1024), "avail_kb": int(free / 1024)})
    return out


def parse_fs(platform: str, out: str) -> tuple[list[dict], list[str]]:
    """回 (掛載點清單, 唯讀重掛的掛載點)。"""
    import health_probe as hp
    seg = _split(out)
    if platform == "windows":
        return _parse_win_vol(seg.get("VOL", "")), []
    if platform == "aix":
        return [d for d in hp.parse_aix_df(seg.get("DF", "")) if ":" not in (d.get("fs") or "")], []   # NFS（host:/path）不算本機
    disks = hp.parse_disks(seg.get("DF", ""), seg.get("DFI", ""))
    ro = [m["mount"] for m in hp.parse_readonly_mounts(seg.get("MOUNTS", ""))]
    return disks, ro


def judge_fs(conn, ip: str, disks: list[dict], ro: list[str] | None = None) -> dict:
    """每個掛載點套 1.5 硬碟同一套判斷（daily_check.judge_live_disks）；inode 與唯讀重掛一起看（D12）。"""
    import daily_check
    import disk_alert
    pats = disk_alert.exclude_list(conn)
    keep = [d for d in disks if not (daily_check._is_swap(d["mount"]) or disk_alert.excluded(d["mount"], pats))]
    if not keep:
        return _no_data("沒有可判定的掛載點（都被排除，或讀不到）")
    items = [{"item": d["mount"], "pct": float(d["use_pct"]), "free_gb": round(d.get("avail_kb", 0) / 1048576, 1),
              "cap_gb": round(d.get("size_kb", 0) / 1048576, 1), "inode_pct": d.get("inode_pct"), "fs": d.get("fs")}
             for d in keep]
    j = daily_check.judge_live_disks(conn, ip, items)
    lst, th = j["list"], j["th"]
    last15 = _last_daily(conn, ip)
    order = {"唯讀重掛": 0, "第一級": 1, "需處理": 2, "已知例外": 3, "正常": 4}
    rows = []
    import capacity_exempt as ce
    for r in j["rows"]:
        ipct = r.get("inode_pct")
        ipct = ipct if ipct is not None and ipct >= 0 else None
        state = r["state"]
        inode_hot = ipct is not None and ipct >= lst
        # inode 自己一套判斷：空間的已知例外不能蓋掉 inode 問題（審查低 3）
        if ipct is not None and state in ("正常", "需處理", "已知例外"):
            if ipct >= th:
                state = "第一級"
            elif ipct >= lst and state in ("正常", "已知例外"):
                state = "需處理"
        if r["item"] in (ro or []):
            state = "唯讀重掛"
        rows.append({"mount": clean(r["item"], 200), "fs": r.get("fs"), "use_pct": r["pct"], "inode_pct": ipct,
                     "size_gb": r["cap_gb"], "avail_gb": r["free_gb"], "level": state, "exempt": r.get("exempt"),
                     "inode_hot": inode_hot, "daily": last15.get(ce.norm_mount(r["item"]) or r["item"])})
    rows.sort(key=lambda r: (order.get(r["level"], 9), -r["use_pct"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    bad = [r for r in rows if r["level"] in ("唯讀重掛", "第一級", "需處理")]
    if bad:
        h = bad[0]
        what = f"inode {h['inode_pct']:g}%" if h["inode_hot"] and h["inode_pct"] and h["inode_pct"] >= h["use_pct"] else f"{h['use_pct']:g}%"
        text = f"{h['mount']} {what}・{h['level']}（需處理 ≥ {lst:g}%、第一級 ≥ {th:g}%{'・單台例外門檻' if j['single'] else ''}）"
        if len(bad) > 1:
            text += f"，另有 {len(bad) - 1} 個"
        code = "readonly" if h["level"] == "唯讀重掛" else "first" if h["level"] == "第一級" else "handle"
        if code == "readonly":
            advice = "檔案系統被系統改成唯讀，常見於磁碟或儲存錯誤：請 SP 立即確認"
        elif h["inode_hot"] and h["inode_pct"] and h["inode_pct"] >= h["use_pct"]:
            advice = "檔案數量（inode）快滿，即使空間還夠也會寫不進檔案：請 AP 清理大量小檔"
        else:
            advice = "請 AP 確認這個目錄的用途與能否清理（第二期會加上目錄大小分析）"
    elif any(r["level"] == "已知例外" for r in rows):
        e = next(r for r in rows if r["level"] == "已知例外")
        text = f"{e['mount']} {e['use_pct']:g}%・已有例外（{e['exempt']['text']}），仍請確認"
        advice = "用量若持續上升請 AP 處理"
        code = "exempt"
    else:
        top = max(rows, key=lambda r: r["use_pct"])
        text = f"沒有掛載點達需處理門檻 {lst:g}%（最高 {top['mount']} {top['use_pct']:g}%）"
        advice = "目前不用處理"
        code = "ok"
    return {"ok": True, "error": None, "mounts": rows, "verdict": code, "verdict_text": text, "advice": advice,
            "rules": {"list": lst, "alert": j["alert"], "th": th, "single": j["single"], "floor": j["floor"]}}


def _last_daily(conn, ip: str) -> dict:
    """1.5 用的 WhatsUp 最近一批完整資料裡這台每顆碟的值（A3）；同一台的每個 IP 都看（D20）。讀取不建表。"""
    import capacity_exempt as ce
    import wug_lastpoll as lp
    try:
        b = lp.latest_complete(conn)
        if not b:
            return {}
        ips = {ip}
        try:
            import manage_state
            r = conn.execute("SELECT asset_serial FROM hardware WHERE TRIM(ip) = ? LIMIT 1", (ip,)).fetchone()
            if r:
                for s in manage_state.same_machine_serials(conn, r[0]) or []:
                    x = conn.execute("SELECT ip FROM hardware WHERE asset_serial = ?", (s,)).fetchone()
                    if x and (x[0] or "").strip():
                        ips.add(x[0].strip())
        except Exception:  # noqa: BLE001 - 找不到同一台的其他 IP 就只看這一個
            pass
        ph = ",".join("?" for _ in ips)
        out = {}
        for r in conn.execute(f"SELECT item, pct, poll_at FROM wug_lp_value WHERE batch_id = ? AND metric = 'disk' AND ip IN ({ph})",
                              (b["batch_id"], *ips)):
            if r["pct"] is not None:
                out[ce.norm_mount(r["item"]) or r["item"]] = {"pct": round(float(r["pct"]), 1), "at": r["poll_at"],
                                                             "batch_at": b.get("collected_at")}
        return out
    except sqlite3.Error:      # 1.5 的表不在／讀不到：這欄顯示「資料不足」，不影響此刻的判斷
        return {}


# ================================================================== 執行與紀錄
_LOCKS: dict = {}
_LOCKS_GUARD = threading.Lock()


def ensure(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS host_diag_run (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT NOT NULL, kind TEXT NOT NULL,
        by_user TEXT, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
        rule_version TEXT, platform TEXT, result TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_host_diag_run_ip ON host_diag_run (ip, kind, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_host_diag_run_started ON host_diag_run (started_at)")


def _ts(d: _dt.datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


def _gate(conn, ip: str, kind: str, by: str | None, now: _dt.datetime) -> None:
    """拿到這台這種分析的鎖之後才檢查（C7：先鎖再查）。用 DB 狀態擋跨行程重複、冷卻、全站上限、每人頻率。"""
    stale = _ts(now - _dt.timedelta(seconds=RUNNING_STALE_SEC))
    conn.execute("UPDATE host_diag_run SET status = 'interrupted', finished_at = ? WHERE status = 'running' AND started_at < ?",
                 (_ts(now), stale))
    if conn.execute("SELECT 1 FROM host_diag_run WHERE ip = ? AND kind = ? AND status = 'running'", (ip, kind)).fetchone():
        raise Busy(f"這台的{KIND_LABEL[kind]}正在跑，請等它跑完")
    r = conn.execute("SELECT id, finished_at FROM host_diag_run WHERE ip = ? AND kind = ? AND status IN ('ok','partial') "
                     "ORDER BY id DESC LIMIT 1", (ip, kind)).fetchone()
    if r and r["finished_at"]:
        try:
            left = COOLDOWN_SEC - (now - _dt.datetime.strptime(r["finished_at"], "%Y-%m-%d %H:%M:%S")).total_seconds()
        except ValueError:
            left = 0
        if left > 0:
            raise Busy(f"這台剛做完{KIND_LABEL[kind]}，{int(left + 0.999)} 秒後可以再按；先看剛才那一筆", r["id"])
    if conn.execute("SELECT COUNT(*) FROM host_diag_run WHERE status = 'running'").fetchone()[0] >= MAX_CONCURRENT:
        raise Busy(f"全站同時在分析的主機已達 {MAX_CONCURRENT} 台，請稍候再按")
    if by and conn.execute("SELECT COUNT(*) FROM host_diag_run WHERE by_user = ? AND started_at >= ?",
                           (by, _ts(now - _dt.timedelta(seconds=60)))).fetchone()[0] >= USER_PER_MIN:
        raise Busy(f"你一分鐘內已按了 {USER_PER_MIN} 次，請稍候")


def _analyze(conn, ip: str, kind: str, platform: str, out: str) -> dict:
    if kind == "cpu":
        return {"linux": analyze_linux_cpu, "aix": analyze_aix_cpu, "windows": analyze_win_cpu}[platform](out)
    disks, ro = parse_fs(platform, out)
    return judge_fs(conn, ip, disks, ro) if disks else _no_data("讀不到 df／磁碟清單")


def run(conn, ip: str, kind: str, platform: str, by: str | None, *, runner, clock=None) -> dict:
    """跑一次分析並存紀錄。runner(platform, kind) → 原始輸出（連線由呼叫端給，測試可注入）。
    runner 丟 NotProbed＝我們這邊的問題；其他例外＝連線失敗；分析本身出錯另記 error（C18：不把程式錯說成連不上）。"""
    if kind not in KINDS:
        raise ValueError(f"不認得的分析：{kind}")
    if platform not in PLATFORMS:
        raise NotProbed(f"這台的平台（{platform}）不支援主機分析")
    clock = clock or _dt.datetime.now
    ensure(conn)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault((ip, kind), threading.Lock())
        if len(_LOCKS) > 2000:                  # 只增不減的保護：清掉沒在用的
            for k in [k for k, v in _LOCKS.items() if not v.locked() and k != (ip, kind)]:
                _LOCKS.pop(k, None)
    if not lock.acquire(blocking=False):
        raise Busy(f"這台的{KIND_LABEL[kind]}正在跑，請等它跑完")
    try:
        now = clock()
        _gate(conn, ip, kind, by, now)
        rid = conn.execute("INSERT INTO host_diag_run (ip, kind, by_user, started_at, status, rule_version, platform) "
                           "VALUES (?,?,?,?,?,?,?)", (ip, kind, by, _ts(now), "running", RULE_VERSION, platform)).lastrowid
        conn.commit()
        status = None
        try:
            out = runner(platform, kind)
        except NotProbed as exc:
            res, status = _no_data(str(exc)[:200]), "not_probed"
        except Exception as exc:  # noqa: BLE001 - 連不上／逾時的原因要原樣講給人看
            res, status = _no_data(f"連線失敗：{str(exc)[:200] or '沒有回應'}"), "failed"
        if status is None:
            try:
                res = _analyze(conn, ip, kind, platform, out)
                status = "ok" if res.get("ok") else "partial"
            except Exception as exc:  # noqa: BLE001 - 解析程式本身的錯：照實寫是我們的問題，不說成主機連不上
                res = _no_data(f"分析程式出錯（{type(exc).__name__}），請回報系統管理者")
                status = "error"
        fin = clock()
        res.update({"id": rid, "ip": ip, "kind": kind, "kind_label": KIND_LABEL[kind], "platform": platform,
                    "started_at": _ts(now), "finished_at": _ts(fin), "by": by, "rule_version": RULE_VERSION})
        conn.execute("UPDATE host_diag_run SET finished_at = ?, status = ?, result = ? WHERE id = ?",
                     (_ts(fin), status, json.dumps(res, ensure_ascii=False), rid))
        if rid % 50 == 0:                        # 清舊結果不必每次做（C24）
            conn.execute("DELETE FROM host_diag_run WHERE started_at < ?", (_ts(now - _dt.timedelta(days=KEEP_DAYS)),))
        conn.commit()
        try:
            import collect_log
            collect_log.record(conn, "host_diag", ip, status == "ok", f"{KIND_LABEL[kind]} {ip}：{res.get('verdict_text', '')}"[:300],
                               actor=by, extra={"kind": kind, "platform": platform, "status": status, "run_id": rid})
        except Exception:  # noqa: BLE001 - 稽核紀錄失敗不影響分析結果（collect_log 自己也不丟例外）
            pass
        res["status"] = status
        res["copy_text"] = copy_text(conn, res)
        return res
    finally:
        lock.release()


def history(conn, ip: str, limit: int = 50) -> list[dict]:
    """讀取不建表（C14）：沒有表＝沒有紀錄。"""
    try:
        rows = conn.execute("SELECT id, kind, by_user, started_at, status, result FROM host_diag_run "
                            "WHERE ip = ? ORDER BY id DESC LIMIT ?", (ip, limit)).fetchall()
    except sqlite3.OperationalError:   # 還沒有人分析過（表還沒建）＝沒有紀錄；讀取路徑不建表
        return []
    out = []
    for r in rows:
        try:
            res = json.loads(r["result"] or "{}")
        except ValueError:
            res = {}
        out.append({"id": r["id"], "kind": r["kind"], "kind_label": KIND_LABEL.get(r["kind"], r["kind"]),
                    "by": r["by_user"], "started_at": r["started_at"], "status": r["status"],
                    "openable": bool(r["result"]),
                    "verdict_text": res.get("verdict_text") or ("中斷" if r["status"] in ("running", "interrupted") else "")})
    return out


def get(conn, ip: str, rid: int) -> dict | None:
    try:
        r = conn.execute("SELECT status, result FROM host_diag_run WHERE id = ? AND ip = ?", (rid, ip)).fetchone()
    except sqlite3.OperationalError:
        return None
    if not r or not r["result"]:
        return None
    try:
        res = json.loads(r["result"])
    except ValueError:
        return None
    res["status"] = r["status"]
    res["historical"] = True
    res["copy_text"] = copy_text(conn, res)           # 負責人每次現查（聯絡人異動後不留舊個資，D19）
    return res


# ================================================================= 複製給群組
def copy_text(conn, res: dict) -> str | None:
    """貼到 Teams 群組的文字：量測／判讀／建議／未確認／負責。不帶帳號密碼、不帶指令列。
    我們這邊的問題（not_probed／error）不給（D15：不把工具的設定缺口報成主機故障）。"""
    if res.get("status") in ("not_probed", "error"):
        return None
    import disk_alert
    ip = res.get("ip") or ""
    try:
        host = disk_alert.host_label(conn, ip)
        who = disk_alert.contact_text(conn, ip)
    except Exception:  # noqa: BLE001 - 查不到負責人也要能複製
        host, who = ip, "資產對不到這個 IP"
    if "對不到" in who or "查詢失敗" in who:
        who = "未登記負責人（" + who + "）"
    when = "歷史快照（非此刻）" if res.get("historical") else "此刻快照"
    lines = [f"【{res.get('kind_label')}】{host}｜{str(res.get('started_at', ''))[:16]}｜{when}", f"系統與負責：{who}"]
    if not res.get("ok"):
        lines.append(f"結果：{res.get('verdict_text')}")
        return "\n".join(lines)
    if res.get("kind") == "cpu":
        parts = [f"程式＋系統 {res['busy_pct']:g}%" if res.get("busy_pct") is not None else "整機讀不到"]
        for k, lab in (("iowait_pct", "等磁碟"), ("steal_pct", "被搶")):
            if res.get(k):
                parts.append(f"{lab} {res[k]:g}%")
        lines.append(f"量測：{'、'.join(parts)}（{res.get('ncpu')} 核，取樣約 {res.get('window_sec') or 2:g} 秒）")
        top = []
        for g in res.get("groups", [])[:3]:
            age = f"，已執行 {g['age_days']:g} 天" if g.get("age_days") is not None else ""
            top.append(f"{_who(g)} {g['pct']:g}%{age}")
        if top:
            lines.append(f"前三名：{'；'.join(top)}")
        unconfirmed = f"只看了約 {res.get('window_sec') or 2:g} 秒；程式用途未確認" \
            + ("；AIX 程式用量為秒級粗估" if res.get("platform") == "aix" else "")
    else:
        bad = [m for m in res.get("mounts", []) if m["level"] != "正常"]
        if bad:
            more = f"；另有 {len(bad) - 3} 個" if len(bad) > 3 else ""
            lines.append("量測：" + "、".join(
                f"{m['mount']} {m['use_pct']:g}%（剩 {m['avail_gb']:g} GB" + (f"，inode {m['inode_pct']:g}%" if m.get("inode_hot") else "")
                + f"）・{m['level']}" for m in bad[:3]) + more)
        unconfirmed = "還沒分析是哪個目錄占用（第二期）；Linux df 扣掉系統保留空間，可能比監控高幾個百分點"
    lines.append(f"判讀：{res.get('verdict_text')}")
    lines.append(f"建議：{res.get('advice')}")
    lines.append(f"未確認：{unconfirmed}")
    return "\n".join(lines)
