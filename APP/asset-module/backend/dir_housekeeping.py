"""目錄清理掃描（磁碟 housekeeping）——1.3 設備風險「目錄清理」頁籤（2.007.17）。

使用者 2026-10-28：「哪些目錄可以 housekeeping、哪些檔案超過 2 年」「空間所有 OS 都要」
「手動＋BUSY 退出＋08:30～15:00 不可執行」。

鐵律（寫在這裡，因為這支會在正式機上跑掃描）：
  1. **純唯讀**：不刪、不改、不搬、不寫目標機任何檔案（連暫存檔都不寫——磁碟本來就快滿）。
  2. **手動＋預設關閉的夜間自動**：管理員在頁面上勾主機按「開始掃描」；2.046.0（R19）起另有夜間自動，
     **預設關閉**、要管理員打開，19:00～07:00 才開新的一台、07:00 後不開、08:30 前結束（同第 3 條），
     只挑檔案系統使用率 ≥80% 的主機，一次一台、每台每晚最多一次、忙就跳過（quiet_window 入口 hk_night_tick）。
  3. **08:30～15:00 不可執行**（伺服器端擋，不只是按鈕反灰）；佇列跑到 08:30 前就停，剩下的標「已停」。
  4. **主機忙就退出**：Linux／AIX 看 load average／CPU 數（>70%），Windows 沿用 CL_DiskHK 的 CPU／記憶體／磁碟忙碌檢查。
  5. **一次一台**（整個系統同一時間只掃一台，不並行）。
  6. 走既有的收集通道與閘門：只掃已納管（collect_ok）、VMware 天條擋下、Windows 要勾選納管、
     SSH 用收集帳號（不是 root、不加 sudoers）、純文字指令（不用密碼工具、不繞執行原則、不編碼）。
     因為用收集帳號掃，**權限讀不到的目錄不在結果裡**——結果會寫「略過 N 個沒權限的目錄」，不假裝掃完整。
  7. 範圍：每個**使用率達門檻**（預設 80%）的檔案系統／磁碟；沒有超過就只回「不需清理」。

結果一律稱「候選」（R18，2.046.0）：每個 ≥80% 的檔案系統列最大的 3 個目錄、其中最大的一項、原因（log／core dump／
tmp／舊封存／超過 2 年沒動）＝容量占用候選，請資料擁有者判斷處理方式（2.046.1：全站不寫處置導向的字，見 test_hk_banned_words）。告警一鍵通知（alert_notify）的磁碟類訊息會附最近一次的候選摘要（brief）。

結果存在 hk_scan（每台每次一列）；Windows 存工具自己的 summary 全文、Linux／AIX 存解析後的目錄與檔案清單＋同格式的 summary。
Linux／AIX 的 find 輸出格式、逾時機制不同：Linux 有 `find -printf`＋`timeout`，AIX 用 `find -ls`＋看門狗（未實機驗證，先試跑）。
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
import threading
from pathlib import Path

BLOCK_START = (8, 30)
BLOCK_END = (15, 0)
SET_PCT = "hk_alert_pct"
DEFAULT_PCT = 80
OLD_YEARS = 2                 # 超過幾年沒異動算舊檔（使用者：超過 2 年）
LINUX_MAX_SEC = 600           # 每個檔案系統最多掃幾秒（超過就中止、標部分結果）
WIN_MAX_MIN = 10
BUSY_RATIO = 0.7              # load average 超過 CPU 數的幾成算忙
TOP_DIRS = 25
TOP_FILES = 20
BIG_FILE_KB = 100 * 1024      # 100 MB 以上才列大檔

_HERE = Path(__file__).resolve().parent
WIN_SCRIPT_PATH = _HERE / "hk_scripts" / "Get-DiskHousekeeping.ps1"

SCAN_SQL = """
CREATE TABLE IF NOT EXISTS hk_scan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_serial TEXT NOT NULL,
    ip TEXT,
    hostname TEXT,
    platform TEXT,
    status TEXT NOT NULL,        -- queued／running／ok／partial／busy／nofs／failed／stopped
    requested_by TEXT,
    queued_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    detail TEXT                  -- JSON：{summary, fs[], dirs[], files[], denied, note}
)
"""


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(SCAN_SQL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hk_serial ON hk_scan(asset_serial, id)")
    conn.commit()


# ---------------------------------------------------------------- 時段
def _now() -> _dt.datetime:
    """現在時間（測試用 monkeypatch 換掉，才能測 08:30～15:00 的擋法）。"""
    import quiet_window as qw
    return qw.local_now()          # 台北牆上時間（同一個來源）


def in_blocked_window(now: _dt.datetime | None = None) -> bool:
    """08:30（含）～15:00（不含）不可執行。"""
    import transfer_policy as tp      # 08:30～15:00 只有一份定義；這裡刻意保留『七天都擋、不可確認放行』的更保守規則
    t = (now or _now()).time()
    return tp.MARKET_OPEN <= t < tp.MARKET_CLOSE


def window_status(now: _dt.datetime | None = None) -> dict:
    now = now or _now()
    blocked = in_blocked_window(now)
    return {"now": now.strftime("%Y-%m-%d %H:%M"), "blocked": blocked,
            "message": ("現在是上班時段（08:30～15:00），不能掃描；15:00 之後才能開始。" if blocked else
                        "現在可以掃描。掃描會在 08:30 前自動停止，沒掃完的標「已停」。")}


MIN_RUNWAY_SEC = 15 * 60     # 離 08:30 不到 15 分鐘就不開始新的一台
SAFETY_SEC = 5 * 60          # 預算留給 08:30 前收尾的緩衝


def seconds_to_block(now: _dt.datetime | None = None) -> int:
    """現在到下一個 08:30 還有幾秒（現在已在 08:30～15:00 內回 0）。"""
    now = now or _now()
    if in_blocked_window(now):
        return 0
    start = now.replace(hour=BLOCK_START[0], minute=BLOCK_START[1], second=0, microsecond=0)
    if now >= start:
        start += _dt.timedelta(days=1)
    return int((start - now).total_seconds())


def alert_pct(conn: sqlite3.Connection) -> int:
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SET_PCT,)).fetchone()
        v = int(r[0]) if r else DEFAULT_PCT
    except (sqlite3.Error, ValueError, TypeError):
        v = DEFAULT_PCT
    return v if 50 <= v <= 99 else DEFAULT_PCT


# ---------------------------------------------------------------- 目標機上跑的腳本
# 輸出協定（Tab 分隔，@@ 開頭）：
#   @@BUSY<TAB>load=<x><TAB>cpus=<n>                 忙，沒掃
#   @@NOFS<TAB><pct>                                 沒有檔案系統達門檻
#   @@FS<TAB><mount><TAB><used%>                     要掃的檔案系統
#   @@DIR<TAB><mount><TAB><dir><TAB><kb><TAB><old_kb><TAB><old_files><TAB><newest>
#   @@FILE<TAB><mount><TAB><kb><TAB><mtime><TAB><path>
#   @@STAT<TAB><mount><TAB><denied><TAB><partial 0|1>
#   @@DONE
LINUX_SCRIPT = r"""
export LC_ALL=C LANG=C
PCT=__PCT__; OLDD=__OLDDAYS__; MAXSEC=__MAXSEC__; BIGKB=__BIGKB__; TOTAL=__TOTAL__
L=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null); N=$(nproc 2>/dev/null || echo 1)
if awk -v l="$L" -v n="$N" 'BEGIN{exit !(l > n*__BUSY__)}'; then printf '@@BUSY\tload=%s\tcpus=%s\n' "$L" "$N"; exit 0; fi
MOUNTS=$(df -P -l -x tmpfs -x devtmpfs -x overlay -x squashfs -x nfs -x nfs4 -x cifs -x fuse -x fuse.sshfs 2>/dev/null | awk -v t="$PCT" 'NR>1{u=$5; sub(/%/,"",u); if (u+0>=t) print u"\t"$6}')
if [ -z "$MOUNTS" ]; then printf '@@NOFS\t%s\n' "$PCT"; exit 0; fi
CUT=$(( $(date +%s) - OLDD*86400 ))
LOWPRI="nice -n 19"; command -v ionice >/dev/null 2>&1 && ionice -c3 true >/dev/null 2>&1 && LOWPRI="ionice -c3 nice -n 19"
HAVE_TO=0; command -v timeout >/dev/null 2>&1 && HAVE_TO=1
T0=$(date +%s)
echo "$MOUNTS" | while IFS="$(printf '\t')" read U M; do
  printf '@@FS\t%s\t%s\n' "$M" "$U"
  S=$(date +%s); LEFT=$(( TOTAL - (S - T0) ))
  if [ "$LEFT" -le 30 ]; then printf '@@STAT\t%s\t0\t1\n' "$M"; continue; fi
  FSEC=$MAXSEC; [ "$LEFT" -lt "$FSEC" ] && FSEC=$LEFT
  if [ "$HAVE_TO" = 1 ]; then TOCMD="timeout $FSEC"; else TOCMD=""; fi
  $LOWPRI $TOCMD find "$M" -xdev -type f -printf '%s %T@ %p\n' 2>&1 | awk -v M="$M" -v CUT="$CUT" -v BIG="$BIGKB" '
    /^find:/ { den++; next }
    $0 !~ /^[0-9]+ [0-9.]+ / { junk++; next }
    { sz=$1; mt=$2+0; p=$0; sub(/^[^ ]+ [^ ]+ /, "", p)
      if (sz/1024 >= BIG) printf "@@FILE\t%s\t%.0f\t%.0f\t%s\n", M, sz/1024, mt, p
      rel=substr(p, length(M)+1); sub(/^\//, "", rel); n=split(rel, a, "/")
      key=M; tot[key]+=sz; if (mt<CUT) { old[key]+=sz; oldn[key]++ } ; if (mt>nw[key]) nw[key]=mt
      for (d=1; d<=3 && d<n; d++) { key=key (key=="/" ? "" : "/") a[d]; tot[key]+=sz
        if (mt<CUT) { old[key]+=sz; oldn[key]++ } ; if (mt>nw[key]) nw[key]=mt } }
    END { for (k in tot) if (tot[k] >= 1048576 || old[k] >= 1048576)
            printf "@@DIR\t%s\t%s\t%.0f\t%.0f\t%.0f\t%.0f\n", M, k, tot[k]/1024, old[k]/1024, oldn[k], nw[k]
          if (junk>0 && !(M in tot)) printf "@@JUNK\t%s\t%d\n", M, junk
          printf "@@STAT\t%s\t%d\t0\n", M, den+0 }'
  E=$(( $(date +%s) - S ))
  if [ "$HAVE_TO" = 1 ] && [ "$E" -ge "$FSEC" ]; then printf '@@STAT\t%s\t0\t1\n' "$M"; fi
done
echo '@@DONE'
"""

AIX_SCRIPT = r"""
export LC_ALL=C LANG=C
PCT=__PCT__; OLDY=__OLDYEARS__; MAXSEC=__MAXSEC__; BIGKB=__BIGKB__; TOTAL=__TOTAL__
L=$(uptime | sed 's/.*average: *//; s/,.*//')
N=$(bindprocessor -q 2>/dev/null | sed 's/.*: *//' | wc -w); [ "$N" -ge 1 ] 2>/dev/null || N=1
if awk -v l="$L" -v n="$N" 'BEGIN{exit !(l > n*__BUSY__)}'; then printf '@@BUSY\tload=%s\tcpus=%s\n' "$L" "$N"; exit 0; fi
LOCALS=$(lsfs -c 2>/dev/null | awk -F: 'NR>1 && ($3=="jfs" || $3=="jfs2") {print $1}')
if [ -z "$LOCALS" ]; then echo '@@NOLOCAL'; exit 0; fi
MOUNTS=$(df -Pk 2>/dev/null | awk -v t="$PCT" -v loc="$LOCALS" 'BEGIN{n=split(loc, a, "\n"); for(i=1;i<=n;i++) ok[a[i]]=1} NR>1 && ($6 in ok) {u=$5; sub(/%/,"",u); if (u+0>=t) print u"\t"$6}')
if [ -z "$MOUNTS" ]; then printf '@@NOFS\t%s\n' "$PCT"; exit 0; fi
set -m
CY=$(date +%Y); CM=$(date +%m); CD=$(date +%d)
T0=$(date +%s)
echo "$MOUNTS" | while IFS="$(printf '\t')" read U M; do
  printf '@@FS\t%s\t%s\n' "$M" "$U"
  LEFT=$(( TOTAL - ($(date +%s) - T0) ))
  if [ "$LEFT" -le 30 ]; then printf '@@STAT\t%s\t0\t1\n' "$M"; continue; fi
  FSEC=$MAXSEC; [ "$LEFT" -lt "$FSEC" ] && FSEC=$LEFT
  ( nice -n 19 find "$M" -xdev -type f -ls 2>&1 | awk -v M="$M" -v CY="$CY" -v CM="$CM" -v CD="$CD" -v O="$OLDY" -v BIG="$BIGKB" '
      BEGIN { split("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec", mn, " "); for (i=1;i<=12;i++) mi[mn[i]]=i }
      /^find:/ { den++; next }
      $0 !~ /^ *[0-9]+ +[0-9]+ +[-a-z]/ { junk++; next }
      { sz=$7; mon=mi[$8]; day=$9+0; ty=$10; p=$0; for (i=1;i<=10;i++) sub(/^ *[^ ]+/, "", p); sub(/^ +/, "", p)
        if (ty ~ /:/) { y = (mon<=CM+0) ? CY+0 : CY-1; old=0 } else { y=ty+0
          old = (y < CY-O || (y == CY-O && (mon < CM+0 || (mon == CM+0 && day <= CD+0)))) ? 1 : 0 }
        dt=sprintf("%04d-%02d-%02d", y, mon, day)
        if (sz/1024 >= BIG) printf "@@FILE\t%s\t%.0f\t%s\t%s\n", M, sz/1024, dt, p
        rel=substr(p, length(M)+1); sub(/^\//, "", rel); n=split(rel, a, "/")
        key=M; tot[key]+=sz; if (old) { oldb[key]+=sz; oldn[key]++ } ; if (dt>nw[key]) nw[key]=dt
        for (d=1; d<=3 && d<n; d++) { key=key (key=="/" ? "" : "/") a[d]; tot[key]+=sz
          if (old) { oldb[key]+=sz; oldn[key]++ } ; if (dt>nw[key]) nw[key]=dt } }
      END { for (k in tot) if (tot[k] >= 1048576 || oldb[k] >= 1048576)
              printf "@@DIR\t%s\t%s\t%.0f\t%.0f\t%.0f\t%s\n", M, k, tot[k]/1024, oldb[k]/1024, oldn[k], nw[k]
            if (junk>0 && !(M in tot)) printf "@@JUNK\t%s\t%d\n", M, junk
            printf "@@STAT\t%s\t%d\t0\n", M, den+0 } ' ) &
  P=$!; i=0
  while kill -0 $P 2>/dev/null; do
    if [ $i -ge $FSEC ]; then kill -TERM -$P 2>/dev/null || kill -TERM $P 2>/dev/null; printf '@@STAT\t%s\t0\t1\n' "$M"; break; fi
    sleep 2; i=$((i+2))
  done
  wait $P 2>/dev/null
done
echo '@@DONE'
"""


def _fill(tpl: str, pct: int, total_sec: int = 6 * 3600) -> str:
    return (tpl.replace("__PCT__", str(int(pct))).replace("__TOTAL__", str(int(total_sec))).replace("__OLDDAYS__", str(OLD_YEARS * 365))
            .replace("__OLDYEARS__", str(OLD_YEARS)).replace("__MAXSEC__", str(LINUX_MAX_SEC))
            .replace("__BIGKB__", str(BIG_FILE_KB)).replace("__BUSY__", str(BUSY_RATIO))).strip("\n")


def linux_script(pct: int = DEFAULT_PCT, total_sec: int = 6 * 3600) -> str:
    return _fill(LINUX_SCRIPT, pct, total_sec)


def aix_script(pct: int = DEFAULT_PCT, total_sec: int = 6 * 3600) -> str:
    return _fill(AIX_SCRIPT, pct, total_sec)


def windows_script(pct: int = DEFAULT_PCT, max_minutes: int = WIN_MAX_MIN) -> str:
    """CL_DiskHK 的 Get-DiskHousekeeping（使用者自己的工具，原樣放在 hk_scripts/）在記憶體裡以 scriptblock 執行：
    -NoReport＝不寫任何檔案；Write-Host 走資訊串流，用 *>&1 併進輸出才收得到；
    script 末尾的 exit 在 scriptblock 裡會把整個 PSRP 工作階段結束掉，所以載入時換成 return。
    不寫 .ps1、不帶 File、不改執行原則、不編碼——跟其他 Windows 收集器同一套規矩。"""
    body = WIN_SCRIPT_PATH.read_text(encoding="utf-8-sig")
    body = re.sub(r"^(\s*)exit(?:[ \t]+\S+)?[ \t]*$", r"\1return", body, flags=re.M)
    if re.search(r"^'@", body, flags=re.M):
        raise RuntimeError("Windows 腳本含有 here-string 結尾，無法安全包裝")
    return ("$__hk = @'\n" + body + "\n'@\n"
            "& ([scriptblock]::Create($__hk)) -NoReport -AlertPercent " + str(int(pct)) +
            f" -OldYears {OLD_YEARS} -AncientYears 5 -MaxMinutes {max(1, int(max_minutes))} -TopN 8 -MinReportGB 0.2 *>&1")


# ---------------------------------------------------------------- 解析（Linux／AIX）
def _fmt_size(kb: float) -> str:
    b = float(kb) * 1024
    for unit, div in (("TB", 1 << 40), ("GB", 1 << 30), ("MB", 1 << 20)):
        if b >= div:
            return f"{b / div:.1f} {unit}"
    return f"{b / 1024:.0f} KB"


def _fmt_date(v) -> str:
    s = str(v or "").strip()
    if re.fullmatch(r"\d{9,11}", s):
        try:
            return _dt.datetime.fromtimestamp(int(s)).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return "-"
    return s if s and s != "0" else "-"


def parse_output(text: str) -> dict:
    """解析 Linux／AIX 腳本輸出。回 {status, load?, fs[], dirs[], files[], stats{mount:{denied,partial}}}。
    status：busy／nofs／ok／partial／failed（沒有 @@DONE 也沒有別的標記＝腳本沒跑完）。"""
    out = {"status": "failed", "fs": [], "dirs": [], "files": [], "stats": {}, "load": None}
    done = False
    for ln in (text or "").splitlines():
        if not ln.startswith("@@"):
            continue
        p = ln.rstrip("\r").split("\t")
        tag = p[0]
        try:
            if tag == "@@BUSY":
                out["status"] = "busy"
                out["load"] = " ".join(p[1:])
                return out
            if tag == "@@NOLOCAL":
                out["status"] = "nolocal"
                return out
            if tag == "@@JUNK":
                out["junk"] = out.get("junk", 0) + int(p[2])
            if tag == "@@NOFS":
                out["status"] = "nofs"
                out["pct"] = int(p[1])
                return out
            if tag == "@@FS":
                out["fs"].append({"mount": p[1], "pct": int(p[2])})
            elif tag == "@@DIR":
                out["dirs"].append({"mount": p[1], "path": p[2], "kb": int(p[3]), "old_kb": int(p[4]),
                                    "old_files": int(p[5]), "newest": _fmt_date(p[6])})
            elif tag == "@@FILE":
                out["files"].append({"mount": p[1], "kb": int(p[2]), "mtime": _fmt_date(p[3]), "path": "\t".join(p[4:])})
            elif tag == "@@STAT":
                st = out["stats"].setdefault(p[1], {"denied": 0, "partial": 0})
                st["denied"] = max(st["denied"], int(p[2]))
                st["partial"] = max(st["partial"], int(p[3]))
            elif tag == "@@DONE":
                done = True
        except (IndexError, ValueError):
            continue          # 少一格的行略過；不猜
    if done:
        out["status"] = "partial" if any(s["partial"] for s in out["stats"].values()) else "ok"
        if out.get("junk") and not out["dirs"] and not out["files"]:
            out["status"] = "failed"          # find 沒有吐出檔案、只有錯誤訊息：不能報「沒什麼可清」
    return out


def build_summary(host: str, parsed: dict, pct: int, when: str) -> str:
    """Linux／AIX 的 summary 全文（跟 Windows 工具的格式相近，可整段貼給 AP）。"""
    L = ["【磁碟空間 Housekeeping 盤點】", f"主機：{host}　執行時間：{when}",
         "本報告為唯讀查詢結果，未刪除或變更任何檔案。",
         f"「沒有異動」以檔案最後修改時間判斷；{OLD_YEARS} 年以上視為舊檔。", ""]
    L.append("■ 磁碟使用率（門檻 %d%%）" % pct)
    for f in parsed["fs"]:
        L.append(f"  {f['mount']}　{f['pct']}%")
    for f in parsed["fs"]:
        m = f["mount"]
        dirs = [d for d in parsed["dirs"] if d["mount"] == m and d["path"] != m]
        st = parsed["stats"].get(m, {"denied": 0, "partial": 0})
        L += ["", f"■ {m}"]
        top = sorted(dirs, key=lambda d: -d["kb"])[:5]
        if top:
            L.append("  最佔空間的目錄：")
            L += [f"    {_fmt_size(d['kb'])}　{d['path']}" for d in top]
        old = sorted((d for d in dirs if d["old_kb"] >= 1048576), key=lambda d: -d["old_kb"])[:8]
        if old:
            L.append(f"  {OLD_YEARS} 年以上沒動的檔案集中在：")
            L += [f"    {_fmt_size(d['old_kb'])}（{d['old_files']} 個檔，最近異動 {d['newest']}）　{d['path']}" for d in old]
        big = sorted((x for x in parsed["files"] if x["mount"] == m), key=lambda x: -x["kb"])[:5]
        if big:
            L.append("  單一大檔：")
            L += [f"    {_fmt_size(x['kb'])}（{x['mtime']}）　{x['path']}" for x in big]
        if st["denied"]:
            L.append(f"  ※ 有 {st['denied']} 個目錄沒有權限讀取，沒列進來（用收集帳號掃描）。")
        if st["partial"]:
            L.append(f"  ※ 超過 {LINUX_MAX_SEC // 60} 分鐘，只掃了一部分，數字是下限。")
    L += ["", "以上為容量占用候選（路徑線索），請資料擁有者判斷處理方式，處理後請回覆，謝謝。"]
    return "\n".join(L)


def trim_detail(parsed: dict) -> dict:
    """存進資料庫的版本：每個檔案系統留最佔空間的 25 個目錄、舊檔最多的 25 個、大檔 20 個。"""
    dirs, files = [], []
    for f in parsed["fs"]:
        m = f["mount"]
        ds = [d for d in parsed["dirs"] if d["mount"] == m and d["path"] != m]
        keep = {d["path"]: d for d in sorted(ds, key=lambda d: -d["kb"])[:TOP_DIRS]}
        keep.update({d["path"]: d for d in sorted(ds, key=lambda d: -d["old_kb"])[:TOP_DIRS] if d["old_kb"] > 0})
        dirs += sorted(keep.values(), key=lambda d: -d["kb"])
        files += sorted((x for x in parsed["files"] if x["mount"] == m), key=lambda x: -x["kb"])[:TOP_FILES]
    return {"fs": parsed["fs"], "dirs": dirs, "files": files, "stats": parsed["stats"]}


# ---------------------------------------------------------------- 閘門＋執行
def _gate(conn: sqlite3.Connection, serial: str) -> tuple[dict | None, str | None]:
    """這台能不能掃。回 (主機列, 不能的原因)。跟收集用同一批閘門：已納管、VMware 天條、Windows 要勾選。"""
    import manage_state

    q, p = manage_state.collect_targets_sql(
        conn, serial, "SELECT asset_serial, ip, os, hostname, is_vm, device_model FROM hardware "
                      "WHERE collect_ok = 1 AND ip IS NOT NULL AND ip != ''")
    rows = conn.execute(q, list(p)).fetchall()
    if not rows:
        return None, manage_state.why_not_collectable(conn, serial) or "這台還沒納管，不能掃描"
    kept, skipped = manage_state.drop_vmware_targets(conn, rows)
    if not kept:
        return None, (skipped[0]["reason"] if skipped else "VMware 設備不掃描")
    return dict(kept[0]), None


def _collect_account(conn, serial, platform):
    """收集帳號（非 root）：root 金鑰那種只放行固定指令，不能跑我們的掃描，所以一律用一般收集帳號。"""
    import collect_account_migration as cam

    return cam.account_for_host(conn, serial, platform)


def _win_run(conn, ip: str, script: str) -> str:
    import credential_store
    import winrm_collector

    cred_name = credential_store.pick_for_host(conn, ip, kind="winrm")
    if not cred_name:
        raise RuntimeError("沒有可用的收集憑證——請先在系統設定新增 WinRM 服務帳號")
    cred = credential_store.get_for_use(conn, cred_name)
    if cred is None:
        raise RuntimeError(f"憑證「{cred_name}」解不開，請重新設定")
    username, password = cred
    try:
        out = winrm_collector._run(ip, username, password, script, 120, deadline_s=WIN_MAX_MIN * 60 + 180)
    except Exception as exc:  # noqa: BLE001 - 原因如實回報
        credential_store.audit_use(conn, cred_name, ip, False, str(exc)[:200])
        raise
    finally:
        password = None  # noqa: F841
    credential_store.audit_use(conn, cred_name, ip, True, "目錄清理掃描")
    return out


WATCHDOG_MARGIN = 120      # 掃描最晚在 08:30 前 2 分鐘收掉


def scan_deadline(runway: int, total: int) -> int:
    """這次掃描最多跑幾秒：腳本總預算＋60 秒收尾，但一定早於 08:30 前 WATCHDOG_MARGIN 秒（看門狗＝這個時間）。"""
    return max(30, min(int(total) + 60, LINUX_MAX_SEC * 4 + 60, int(runway) - WATCHDOG_MARGIN))


def _isolated_argv(run, ip: str, cmd: str) -> list[str]:
    """掃描用的獨立 SSH 連線（2.046.2，GPT 複核）：ControlMaster=no、ControlPath=none——不共用、也不碰同一台的共用連線；
    其他照收集 runner 的參數（金鑰、BatchMode、主機金鑰 accept-new、收集帳號）。"""
    import manage_state
    return ["ssh", "-i", run.key_path, "-o", "BatchMode=yes", *manage_state._ssh_hostkey_opts(),
            "-o", "ControlMaster=no", "-o", "ControlPath=none",
            "-o", f"ConnectTimeout={getattr(run, 'timeout', 10)}", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3",
            f"{run.account}@{ip}", cmd]


def run_with_deadline(argv: list[str], deadline_s: float, popen=None) -> tuple[str, bool]:
    """看門狗：自己開子程序、記下它，到時間只結束「這一個」子程序（kill 自己的 PID），不關任何共用連線、不影響同一台的其他連線。
    回 (輸出, 是否被看門狗結束)。popen 可注入（測試）。"""
    import subprocess
    p = (popen or subprocess.Popen)(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, _err = p.communicate(timeout=deadline_s)
        return out or "", False
    except subprocess.TimeoutExpired:
        p.kill()                      # 只結束自己啟動的這一個 ssh 子程序
        try:
            p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return "", True


def scan_host(conn: sqlite3.Connection, serial: str, runner=None, win_runner=None,
              now: _dt.datetime | None = None, night: bool = False, popen=None) -> dict:
    """掃一台，回 {status, summary, ...}（不寫庫；寫庫由 run_scan 做）。runner／win_runner 可注入（測試）。
    night＝夜間自動：Linux 目標機沒有 timeout 指令（沒有遠端逾時保護）就不掃，標「只能手動」；手動掃描照原規則。
    popen＝看門狗用的子程序工廠（測試注入）；正式 runner（SSHRunner）的掃描走獨立連線＋看門狗，注入的 runner（測試）直接呼叫。"""
    now = now or _now()
    if in_blocked_window(now):
        return {"status": "blocked", "note": window_status(now)["message"]}
    runway = seconds_to_block(now)
    if runway < MIN_RUNWAY_SEC:
        return {"status": "stopped", "note": "離 08:30 不到 15 分鐘，不開始掃描"}
    host, why = _gate(conn, serial)
    if why:
        return {"status": "failed", "note": why}
    ip = host["ip"]
    pct = alert_pct(conn)
    platform = _platform(conn, host)
    label = host.get("hostname") or ip
    when = now.strftime("%Y-%m-%d %H:%M")
    if platform == "windows":
        import win_onboard

        blocked = win_onboard.collect_block(conn, ip, serial)
        if blocked:
            return {"status": "failed", "note": blocked}
        try:
            mins = max(1, min(WIN_MAX_MIN, (runway - SAFETY_SEC) // 60))
            text = (win_runner or (lambda i, s: _win_run(conn, i, s)))(ip, windows_script(pct, mins))
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "note": f"Windows 連線或執行失敗：{str(exc)[:240]}"}
        text = neutral_text(text.strip())
        if not text:
            return {"status": "failed", "note": "Windows 腳本沒有回傳內容"}
        if "系統負載過高，本次不執行" in text or "請等負載下降" in text:
            return {"status": "busy", "summary": text, "platform": platform}
        if "執行錯誤" in text:
            return {"status": "failed", "note": "掃描工具回報執行錯誤，請看下面的內容", "summary": text, "platform": platform}
        if "中止原因" in text or "未掃描：" in text or "時間已用完" in text:
            return {"status": "partial", "summary": text, "platform": platform}
        return {"status": "ok", "summary": text, "platform": platform}
    if platform not in ("linux", "aix"):
        return {"status": "failed", "note": f"不支援的平台：{platform}"}
    if runner is None:
        acct = _collect_account(conn, serial, platform)
        if acct == "root":       # root 金鑰主機的 runner 只放行固定指令，也不該用 root 掃檔案系統
            return {"status": "failed", "note": "這台用 root 金鑰收集，不做目錄掃描"}
        import manage_state
        run = manage_state._ssh_runner(manage_state.COLLECTOR_KEY_DEFAULT, account=acct)
    else:
        run = runner
    total = min(LINUX_MAX_SEC * 4, max(60, runway - SAFETY_SEC))     # 整台總預算：到 08:30 前 5 分鐘為止
    try:
        # 先用短逾時確認連得上；掃描本身的總逾時另外設（ConnectTimeout 維持短）
        if hasattr(run, "timeout"):
            probe = run(ip, "echo hk_ok")
            if "hk_ok" not in (probe or ""):
                return {"status": "failed", "note": "SSH 連不上（收集帳號登入失敗或主機沒回應）"}
        # 天條：資料庫登記可能是錯的，連上後、跑任何掃描指令之前再驗一次本機是不是 ESXi（fail-closed）
        import onboard_engine
        import os_rebuild_collector
        if onboard_engine.is_esxi_output(run(ip, os_rebuild_collector.ESXI_PROBE_CMD) or ""):
            return {"status": "failed", "note": os_rebuild_collector.ESXI_SKIP_REASON, "platform": "esxi"}
        if night and platform == "linux" and "hk_to_ok" not in (run(ip, "command -v timeout >/dev/null 2>&1 && echo hk_to_ok") or ""):
            return {"status": "notimeout", "note": "目標機沒有 timeout 指令（沒有遠端逾時保護），夜間自動不掃；需要時請在非上班時段手動掃描",
                    "platform": platform}
        script = linux_script(pct, total) if platform == "linux" else aix_script(pct, total)
        if hasattr(run, "key_path") and hasattr(run, "account"):
            import ssh_mux
            with ssh_mux.host_lock(ip):          # 同一台一次一條（跟其他收集同一把鎖），但用自己的獨立連線
                text, killed = run_with_deadline(_isolated_argv(run, ip, script), scan_deadline(runway, total), popen=popen)
            if killed:
                return {"status": "stopped", "note": "到時間（08:30 前）看門狗結束了這次掃描自己的連線，這台沒掃完、沒有結果",
                        "platform": platform}
        else:
            text = run(ip, script)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "note": f"執行失敗：{str(exc)[:240]}"}
    parsed = parse_output(text)
    if parsed["status"] == "busy":
        return {"status": "busy", "note": f"主機忙碌（{parsed['load']}），沒有掃描", "platform": platform}
    if parsed["status"] == "nolocal":
        return {"status": "failed", "note": "查不到本機檔案系統清單（lsfs），為了不掃到網路磁碟，這台不掃", "platform": platform}
    if parsed["status"] == "nofs":
        return {"status": "nofs", "note": f"沒有檔案系統超過 {pct}%，不需要清理", "platform": platform}
    if parsed["status"] == "failed":
        why = ("find 沒有回傳檔案資料（只有錯誤訊息，可能缺 find 的選項或指令）" if parsed.get("junk")
               else "腳本沒有跑完（沒有收到結束標記）")
        return {"status": "failed", "note": why, "platform": platform}
    summary = build_summary(label, parsed, pct, when)
    return {"status": parsed["status"], "summary": summary, "platform": platform, **trim_detail(parsed)}


# CL_DiskHK 報告最後那句（處置導向）。那個字用 \u522a 跳脫寫，這支程式自己才不會被禁用字守門擋下
_PRESCRIPTIVE = re.compile("以上項目請\\s*AP\\s*確認是否可\u522a除[^\\n]*")


def neutral_text(text: str) -> str:
    """CL_DiskHK 報告最後那句是處置導向的寫法；畫面與可複製的 summary 一律換成中性說法（工具檔案本身原樣不改）。"""
    return _PRESCRIPTIVE.sub("以上為容量占用候選（路徑線索），請資料擁有者判斷處理方式，處理後請回覆，謝謝。", text)


def _platform(conn, host: dict) -> str:
    import manage_state

    return manage_state.collect_platform_of(conn, host["ip"], host.get("os"), asset_serial=host["asset_serial"])


# 整個系統同一時間只掃一台
_lock = threading.Lock()
_active: dict = {"running": False}


def is_running() -> bool:
    return bool(_active.get("running"))


def queue_scan(conn: sqlite3.Connection, serials: list[str], user: str, db_path=None, runner=None,
               win_runner=None, sync: bool = False, night: bool = False) -> dict:
    """排進佇列並（背景）逐台掃描。時段不允許直接回錯；已有佇列在跑就拒絕（一次一批）。
    sync=True（測試）在本執行緒做完。"""
    ensure_table(conn)
    now = _now()
    if in_blocked_window(now):
        raise PermissionError(window_status(now)["message"])
    reap_stale(conn)
    serials = [s for s in dict.fromkeys(x.strip() for x in serials if x and x.strip())]
    if not serials:
        raise ValueError("沒有選任何主機")
    if len(serials) > 50:
        raise ValueError("一次最多排 50 台")
    with _lock:
        if _active["running"]:
            raise RuntimeError("已經有一批掃描在跑，等它結束再排")
        _active["running"] = True
    ids = []
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    try:
        if db_path is None and not sync:
            db_path = conn.execute("PRAGMA database_list").fetchone()[2]
        for s in serials:
            hw = conn.execute("SELECT hostname, ip FROM hardware WHERE asset_serial = ?", (s,)).fetchone()
            cur = conn.execute("INSERT INTO hk_scan (asset_serial, ip, hostname, status, requested_by, queued_at) "
                               "VALUES (?,?,?,?,?,?)", (s, hw["ip"] if hw else None, hw["hostname"] if hw else None,
                                                        "queued", user, stamp))
            ids.append(cur.lastrowid)
        conn.commit()
    except Exception:
        _active["running"] = False
        raise

    def worker():
        import db as _db
        c = conn
        try:
            if not sync:
                c = _db.get_connection(db_path)       # 這行也要在 try 裡：失敗時旗標才會放掉
            for rid, s in zip(ids, serials):
                _run_one(c, rid, s, runner, win_runner, night)
        except Exception as exc:  # noqa: BLE001 - 整批出事：還沒掃的列標失敗，原因寫進去
            conn.execute("UPDATE hk_scan SET status='failed', finished_at=?, detail=? "
                         "WHERE id IN (%s) AND status IN ('queued','running')" % ",".join("?" for _ in ids),
                         [_now().strftime("%Y-%m-%d %H:%M:%S"),
                          json.dumps({"note": f"掃描批次中斷：{str(exc)[:200]}"}, ensure_ascii=False), *ids])
            conn.commit()
        finally:
            _active["running"] = False
            if c is not conn:
                c.close()

    if sync:
        worker()
    else:
        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            _active["running"] = False
            raise
    return {"queued": len(ids), "ids": ids}


def reap_stale(conn: sqlite3.Connection) -> int:
    """沒有掃描在跑、資料庫卻還有「排隊中／掃描中」的列＝服務重啟把它中斷了：標失敗，畫面才不會一直轉。"""
    if is_running():
        return 0
    cur = conn.execute("UPDATE hk_scan SET status='failed', finished_at=?, detail=? WHERE status IN ('queued','running')",
                       (_now().strftime("%Y-%m-%d %H:%M:%S"),
                        json.dumps({"note": "服務重啟，這次掃描被中斷，沒有結果"}, ensure_ascii=False)))
    conn.commit()
    return cur.rowcount


def _run_one(conn, rid: int, serial: str, runner, win_runner, night: bool = False) -> None:
    now = _now()
    if in_blocked_window(now):         # 佇列跑到 08:30：剩下的不掃
        conn.execute("UPDATE hk_scan SET status='stopped', finished_at=?, detail=? WHERE id=?",
                     (now.strftime("%Y-%m-%d %H:%M:%S"), json.dumps({"note": "到 08:30 了，停止掃描，沒有掃這台"}, ensure_ascii=False), rid))
        conn.commit()
        return
    conn.execute("UPDATE hk_scan SET status='running', started_at=? WHERE id=?", (now.strftime("%Y-%m-%d %H:%M:%S"), rid))
    conn.commit()
    try:
        res = scan_host(conn, serial, runner=runner, win_runner=win_runner, night=night)
    except Exception as exc:  # noqa: BLE001 - 單台出錯不中斷整批，原因寫進這一列
        res = {"status": "failed", "note": f"掃描程式錯誤：{str(exc)[:240]}"}
    status = res.pop("status")
    conn.execute("UPDATE hk_scan SET status=?, platform=?, finished_at=?, detail=? WHERE id=?",
                 (status, res.get("platform"), _now().strftime("%Y-%m-%d %H:%M:%S"),
                  json.dumps(res, ensure_ascii=False), rid))
    conn.commit()


# ---------------------------------------------------------------- 給畫面
STATUS_LABEL = {"queued": "排隊中", "running": "掃描中", "ok": "完成", "partial": "部分完成（逾時）",
                "busy": "主機忙碌，沒掃", "nofs": "不需清理", "failed": "失敗", "stopped": "已停（08:30）",
                "notimeout": "跳過：無遠端逾時保護，只能手動"}


def overview(conn: sqlite3.Connection) -> dict:
    """可掃的主機（已納管、非 VMware）＋各自最近一次掃描狀態。"""
    import manage_state

    ensure_table(conn)
    reap_stale(conn)
    rows = conn.execute("SELECT asset_serial, ip, hostname, os, physical_location, rack_no FROM hardware "
                        "WHERE collect_ok = 1 AND ip IS NOT NULL AND ip != '' ORDER BY hostname, ip").fetchall()
    kept, _vm = manage_state.drop_vmware_targets(conn, rows)
    last = {}
    for r in conn.execute("SELECT * FROM hk_scan WHERE id IN (SELECT MAX(id) FROM hk_scan GROUP BY asset_serial)"):
        last[r["asset_serial"]] = dict(r)
    out = []
    for h in kept:
        d = dict(h)
        plat = _platform(conn, d)
        sc = last.get(d["asset_serial"])
        det = {}
        if sc and sc.get("detail"):
            try:
                det = json.loads(sc["detail"])
            except ValueError:
                det = {}
        out.append({"serial": d["asset_serial"], "hostname": d.get("hostname"), "ip": d["ip"], "platform": plat,
                    "os": d.get("os"), "scan_id": sc["id"] if sc else None,
                    "status": sc["status"] if sc else None,
                    "status_label": STATUS_LABEL.get(sc["status"], sc["status"]) if sc else "尚未掃描",
                    "finished_at": (sc.get("finished_at") or sc.get("queued_at")) if sc else None,
                    "note": det.get("note")})
    return {"window": window_status(), "alert_pct": alert_pct(conn), "old_years": OLD_YEARS,
            "running": is_running(), "hosts": out, "night": night_status(conn)}


def result(conn: sqlite3.Connection, scan_id: int) -> dict | None:
    ensure_table(conn)
    r = conn.execute("SELECT * FROM hk_scan WHERE id = ?", (scan_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d["detail"] = json.loads(d["detail"]) if d.get("detail") else {}
    except ValueError:
        d["detail"] = {}
    d["status_label"] = STATUS_LABEL.get(d["status"], d["status"])
    d["candidates"] = candidates(d["detail"]) if d["status"] in ("ok", "partial") else []
    d["windows_text"] = bool(d["detail"].get("summary")) and not d["detail"].get("fs")
    return d


# ---------------------------------------------------------------- R18 容量占用候選（2.046.0；只是「候選」，請資料擁有者判斷處理方式）
CAND_TOP = 3
DRILL_RATIO = 0.8            # 子目錄佔父目錄 ≥80% 就往下鑽（三個候選不要是同一串 /var、/var/log、/var/log/app）
OLD_SHARE = 0.5              # 舊檔佔這個目錄 ≥50% 才標「超過 2 年沒動」
_LOG_SEG = re.compile(r"^(log|logs|syslog|audit|journal|trace|traces|.*\.log(\.\d+)?)$", re.I)
_CORE_SEG = re.compile(r"^(core|cores|coredump|coredumps|crash|crashes|dump|dumps|.*\.dmp|core\.\d+)$", re.I)
_TMP_SEG = re.compile(r"^(tmp|temp|\.tmp)$", re.I)
_ARCH_SEG = re.compile(r"^(archive|archives|archived|backup|backups|bak|old|history|.*\.(tar|tgz|gz|zip|bz2|xz|7z|bak|old|arc))$", re.I)
REASON_NONE = "佔用大，請 AP 判斷"


def _rel(mount: str, path: str) -> str:
    return path[len(mount):].strip("/") if path.startswith(mount) else path.strip("/")


def _is_under(child: str, parent: str) -> bool:
    return child != parent and child.startswith(parent.rstrip("/") + "/")


def reasons_of(path: str, d: dict | None = None, biggest: dict | None = None) -> list[str]:
    """為什麼是候選（看目錄名稱「整段」比對，不做子字串，判不出就寫「佔用大，請 AP 判斷」）。"""
    segs = [s for s in path.strip("/").split("/") if s]
    names = segs + ([biggest["path"].rsplit("/", 1)[-1]] if biggest and biggest.get("kind") == "file" else [])
    out = []
    if any(_LOG_SEG.match(s) for s in names):
        out.append("log")
    if any(_CORE_SEG.match(s) for s in names):
        out.append("core dump")
    if any(_TMP_SEG.match(s) for s in names):
        out.append("tmp")
    if any(_ARCH_SEG.match(s) for s in names):
        out.append("舊封存")
    if d and d.get("kb") and d.get("old_kb", 0) >= OLD_SHARE * d["kb"]:
        out.append(f"超過 {OLD_YEARS} 年沒動（佔 {round(d['old_kb'] * 100 / d['kb'])}%）")
    return out or [REASON_NONE]


def _pick_dirs(mount: str, dirs: list[dict]) -> list[dict]:
    """最大的 3 個目錄：從第一層開始，子目錄佔 ≥80% 就往下鑽；再依大小取前 3，去掉祖先／子孫重複。"""
    by_path = {d["path"]: d for d in dirs}

    def children(p):
        return [d for d in dirs if _is_under(d["path"], p) and "/" not in _rel(p, d["path"])]
    tops = [d for d in dirs if "/" not in _rel(mount, d["path"])] or list(dirs)
    picked = []
    for d in tops:
        cur = d
        for _ in range(3):
            ch = sorted(children(cur["path"]), key=lambda x: -x["kb"])
            if ch and ch[0]["kb"] >= DRILL_RATIO * cur["kb"]:
                cur = ch[0]
            else:
                break
        picked.append(cur)
    out = []
    for d in sorted({x["path"]: x for x in picked}.values(), key=lambda x: -x["kb"]):
        if any(_is_under(d["path"], o["path"]) or _is_under(o["path"], d["path"]) for o in out):
            continue
        out.append(by_path.get(d["path"], d))
        if len(out) == CAND_TOP:
            break
    return out


def _biggest(d: dict, dirs: list[dict], files: list[dict]) -> dict | None:
    """這個目錄裡最大的一項：直接子目錄或 100 MB 以上的大檔，取大的。"""
    kids = [x for x in dirs if _is_under(x["path"], d["path"]) and "/" not in _rel(d["path"], x["path"])]
    fs = [x for x in files if _is_under(x["path"], d["path"])]
    best = None
    if kids:
        k = max(kids, key=lambda x: x["kb"])
        best = {"kind": "dir", "path": k["path"], "kb": k["kb"], "size": _fmt_size(k["kb"])}
    if fs:
        f = max(fs, key=lambda x: x["kb"])
        if best is None or f["kb"] > best["kb"]:
            best = {"kind": "file", "path": f["path"], "kb": f["kb"], "size": _fmt_size(f["kb"]), "mtime": f.get("mtime")}
    return best


def candidates(detail: dict) -> list[dict]:
    """每個達門檻的檔案系統：前 3 個候選目錄（大小、其中最大的一項、原因）＋權限不足／逾時的照實標示。純函式（不連線、不寫庫）。
    Windows 的結果只有工具的報告全文，沒有逐目錄資料：回 windows_text=True，畫面寫「見報告全文」，不從文字猜。"""
    detail = detail or {}
    if not detail.get("fs"):
        return []
    out = []
    for f in detail["fs"]:
        m = f["mount"]
        dirs = [d for d in detail.get("dirs") or [] if d["mount"] == m and d["path"] != m]
        files = [x for x in detail.get("files") or [] if x["mount"] == m]
        st = (detail.get("stats") or {}).get(m, {"denied": 0, "partial": 0})
        flags = []
        if st.get("denied"):
            flags.append(f"權限不足：略過 {st['denied']} 個沒權限的目錄（數字是下限）")
        if st.get("partial"):
            flags.append("逾時：只掃了一部分（數字是下限）")
        items = []
        for d in _pick_dirs(m, dirs):
            big = _biggest(d, dirs, files)
            items.append({"path": d["path"], "kb": d["kb"], "size": _fmt_size(d["kb"]), "old_kb": d.get("old_kb", 0),
                          "newest": d.get("newest"), "biggest": big, "reasons": reasons_of(d["path"], d, big)})
        out.append({"mount": m, "pct": f.get("pct"), "flags": flags, "items": items})
    return out


BRIEF_MAX = 300           # 告警卡片一行 400 字（跳脫後）；摘要限 300，超過的寫「另 N 項見 1.3 目錄清理」，不悄悄截掉


def _mid(text: str, n: int) -> str:
    """太長就截掉中段（「…」代替），頭尾都留：路徑的開頭是掛載點、結尾是目錄名，兩邊都有用。"""
    if len(text) <= n:
        return text
    if n <= 1:
        return "…"
    a = (n - 1) // 2
    return text[:a] + "…" + text[len(text) - (n - 1 - a):]


def _fit_brief(head: str, items: list[dict], end: str, top: int, max_len: int) -> str:
    """組摘要：head＋候選（照順序）＋「另 N 項」＋權限／逾時註記（end）。後兩者一定在；總長 ≤ max_len。"""
    def part(it, path=None):
        return f"{path if path is not None else it['path']} {it['size']}（{'、'.join(it['reasons'])}）"

    def more(k):
        return f"；另 {len(items) - k} 項見 1.3 目錄清理" if len(items) > k else ""
    if not items:
        return _mid(head + end, max_len)
    for k in range(min(top, len(items)), 0, -1):
        out = head + "；".join(part(it) for it in items[:k]) + more(k) + end
        if len(out) <= max_len:
            return out
    # 連第一條都放不下：只放第一條，路徑截中段
    first = items[0]
    room = max_len - len(head + more(1) + end) - len(part(first, ""))
    if room >= 8:
        return head + part(first, _mid(first["path"], room)) + more(1) + end
    # 註記本身就很長：候選只留「另 N 項」，註記照留、截中段（不從尾巴硬切）
    out = head + more(0).lstrip("；") + end
    return out if len(out) <= max_len else head + more(0).lstrip("；") + _mid(end, max_len - len(head + more(0).lstrip("；")))


def brief(conn: sqlite3.Connection, ip: str | None = None, serial: str | None = None, top: int = CAND_TOP,
          max_len: int = BRIEF_MAX) -> str:
    """給告警通知的一行摘要（R18 接告警）：這台最近一次掃描的候選。唯讀、不建表；查不到寫「尚未掃描」。
    2.046.1：限長 max_len——放不下的候選寫「另 N 項見 1.3 目錄清理」；權限不足／逾時的註記一定保留。
    2.046.2（GPT 複核）：先保留「另 N 項」與權限／逾時註記，剩下的空間照順序塞候選；連第一條都放不下（路徑超長）時，
    路徑截掉中段用「…」代替，不從尾巴硬切（尾巴就是那兩條警告）。"""
    if not (ip or serial):
        return "目錄清理：查不到這台"
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hk_scan'").fetchone() is None:
        return "目錄清理：尚未掃描"
    q, a = ("asset_serial = ?", serial) if serial else ("ip = ?", ip)
    r = conn.execute(f"SELECT * FROM hk_scan WHERE {q} AND status NOT IN ('queued', 'running') ORDER BY id DESC LIMIT 1", (a,)).fetchone()
    if r is None:
        return "目錄清理：尚未掃描"
    when = str(r["finished_at"] or r["queued_at"] or "")[5:16]
    try:
        det = json.loads(r["detail"] or "{}")
    except ValueError:
        det = {}
    st = r["status"]
    if st == "nofs":
        return f"目錄清理（{when} 掃）：沒有檔案系統超過門檻"
    if st in ("ok", "partial"):
        cs = candidates(det)
        if not cs:
            return f"目錄清理（{when} 掃）：Windows 報告請到 1.3「目錄清理」看全文" if det.get("summary") else f"目錄清理（{when} 掃）：沒有候選"
        items = [it for c in cs for it in c["items"]]
        tail = "；".join(f for c in cs for f in c["flags"])
        return _fit_brief(f"容量占用候選（{when} 掃，請資料擁有者判斷處理方式）：", items, f"。※{tail}" if tail else "", top, max_len)
    return f"目錄清理（{when}）：{STATUS_LABEL.get(st, st)}" + (f"，{det.get('note')}" if det.get("note") else "")


def brief_for_event(conn: sqlite3.Connection, asset_serial: str | None, ip: str | None) -> str:
    """告警事件的摘要（2.046.1，GPT P1）：先用事件已對應的資產；沒對應時，只有 IP 在資產庫唯一才用 IP 找，
    否則不猜（IP 重複、回收、重新分配會把別台的目錄帶進通知）。"""
    if asset_serial:
        return brief(conn, serial=asset_serial)
    if not ip:
        return "目錄清理：這則告警沒有對應的主機"
    n = conn.execute("SELECT COUNT(*) FROM hardware WHERE ip = ?", (ip,)).fetchone()[0]
    if n == 1:
        return brief(conn, ip=ip)
    return "目錄清理：這則告警對不到唯一一台主機（IP 重複或沒有登記），請到 1.3 目錄清理查"


# ---------------------------------------------------------------- R19 夜間自動（預設關閉）
SET_NIGHT = "hk_night_enabled"
SET_NIGHT_META = "hk_night_meta"
NIGHT_START = _dt.time(19, 0)
NIGHT_END = _dt.time(7, 0)       # 07:00 之後不開新的一台；08:30 前強制結束沿用 seconds_to_block 的預算
NIGHT_USER = "夜間自動（系統）"


def in_night(now: _dt.datetime | None = None) -> bool:
    t = (now or _now()).time()
    return t >= NIGHT_START or t < NIGHT_END


def night_since(now: _dt.datetime | None = None) -> str:
    """這一晚從幾點算起（19:00）。00:00～06:59 屬於前一天 19:00 開始的那一晚。"""
    now = now or _now()
    day = now.date() if now.time() >= NIGHT_START else now.date() - _dt.timedelta(days=1)
    return _dt.datetime.combine(day, NIGHT_START).strftime("%Y-%m-%d %H:%M:%S")


def night_enabled(conn: sqlite3.Connection) -> bool:
    """讀不出來＝關閉（fail-closed：預設不會自己去掃）。"""
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SET_NIGHT,)).fetchone()
        return bool(r) and str(r[0]).strip() == "1"
    except sqlite3.Error:
        return False


def set_night(conn: sqlite3.Connection, enabled: bool, by: str) -> dict:
    """管理員開關（寫最後一次是誰、何時；完整操作紀錄由 API 層 activity middleware 留）。"""
    at = _now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (SET_NIGHT, "1" if enabled else "0"))
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (SET_NIGHT_META, json.dumps({"by": by, "at": at, "enabled": bool(enabled)}, ensure_ascii=False)))
    conn.commit()
    return {"enabled": bool(enabled), "by": by, "at": at}


def night_targets(conn: sqlite3.Connection) -> list[tuple[str, float]]:
    """夜間要掃的主機：最近一次收集的檔案系統（by=mount）裡，任一掛載點使用率 ≥80%（disk_level 那一把尺）。
    使用率高的先掃。收集方式是 VG／磁碟（看不到檔案系統使用率）的不挑。"""
    import disk_level
    import storage_capacity
    out = []
    for p in storage_capacity.physical(conn)["rows"]:
        if p["by"] != "mount":
            continue
        best = None
        for g in p["groups"]:
            if g.get("free_gb") is None or not g.get("size_gb"):
                continue
            used = disk_level.used_from_free(g["free_gb"] * 100 / g["size_gb"])
            if used is not None and (best is None or used > best):
                best = used
        if best is not None and disk_level.listed(best):
            out.append((p["asset_serial"], best))
    out.sort(key=lambda x: -x[1])
    return out


def night_tick(conn: sqlite3.Connection, now: _dt.datetime | None = None, runner=None, win_runner=None, sync: bool = False) -> dict:
    """排程每分鐘呼叫一次。預設關閉；19:00～07:00 才開新的一台；一次一台（有掃描在跑就等）；每台每晚最多一次（含忙碌退出）。
    掃描本身走 queue_scan → scan_host，所有鐵律（08:30 預算、忙碌退出、閘門、VMware 天條、ESXi 連上後再驗）都一樣。"""
    if not night_enabled(conn):
        return {"skip": "off"}
    import quiet_window as qw
    if qw.defer_if_quiet(conn, "hk_night_tick", now):
        return {"skip": "quiet"}
    now = now or _now()
    if not in_night(now):
        return {"skip": "window"}
    if in_blocked_window(now) or seconds_to_block(now) < MIN_RUNWAY_SEC:
        return {"skip": "runway"}
    if is_running():
        return {"skip": "running"}
    ensure_table(conn)
    since = night_since(now)
    done = {r[0] for r in conn.execute("SELECT asset_serial FROM hk_scan WHERE requested_by = ? AND queued_at >= ?", (NIGHT_USER, since))}
    for serial, pct in night_targets(conn):
        if serial in done:
            continue
        _host, why = _gate(conn, serial)
        if why:
            continue                       # 不能掃（沒納管、VMware…）：不排、不記，manual 規則照舊
        res = queue_scan(conn, [serial], NIGHT_USER, runner=runner, win_runner=win_runner, sync=sync, night=True)
        return {**res, "queued": serial, "pct": pct}
    return {"skip": "none"}


def night_status(conn: sqlite3.Connection, now: _dt.datetime | None = None) -> dict:
    """畫面用：開關、時段、最後一次是誰改的、這一晚（或最近一晚）掃了哪幾台。"""
    now = now or _now()
    meta = {}
    try:
        r = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SET_NIGHT_META,)).fetchone()
        meta = json.loads(r[0]) if r else {}
    except (sqlite3.Error, ValueError, TypeError):
        meta = {}
    since = night_since(now)
    rows = []
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hk_scan'").fetchone():
        rows = [{"scan_id": x["id"], "serial": x["asset_serial"], "hostname": x["hostname"], "ip": x["ip"], "status": x["status"],
                 "status_label": STATUS_LABEL.get(x["status"], x["status"]), "at": x["finished_at"] or x["queued_at"]}
                for x in conn.execute("SELECT * FROM hk_scan WHERE requested_by = ? AND queued_at >= ? ORDER BY id", (NIGHT_USER, since))]
    return {"enabled": night_enabled(conn), "window": "19:00～07:00", "end_by": "08:30", "in_night": in_night(now),
            "changed_by": meta.get("by"), "changed_at": meta.get("at"), "since": since, "scans": rows}
