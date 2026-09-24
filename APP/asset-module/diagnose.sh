#!/usr/bin/env bash
# 戰情室 · 一鍵健檢
# ---------------------------------------------------------------
# 出狀況時跑這支，把整份輸出貼回給 AI，不必再一條一條複製指令。
#
#   用法：   sudo bash diagnose.sh
#   結果：   畫面會印，同時存一份到 /opt/webit3/data/logs/diagnose_<時間>.txt
#
# 唯讀：只查詢不修改，不重啟服務、不動資料，正式機上隨時可跑。
# ---------------------------------------------------------------
# 輸出預設以「統計數字」為主，明細只列少量範例。
# 這些內容會被貼到對話裡，主機名與 IP 屬於內網資訊，能少帶就少帶；
# 真的需要看明細時再加 --detail。
set -uo pipefail

DETAIL=0
[ "${1:-}" = "--detail" ] && DETAIL=1

DATA="${WEBIT_DATA:-/opt/webit3/data}"
APP="${WEBIT_APP:-/opt/webit3/app}"
VENV="${WEBIT_VENV:-/opt/webit3/venv}"
DB="${ASSET_DB_PATH:-$DATA/asset.db}"
LOG_DIR="$DATA/logs"; mkdir -p "$LOG_DIR" 2>/dev/null || LOG_DIR=/tmp
LOG="$LOG_DIR/diagnose_$(date +%Y%m%d_%H%M%S).txt"
exec > >(tee "$LOG") 2>&1

sec() { echo; echo "═══ $* ═══"; }

echo "戰情室 · 健檢報告"
echo "時間： $(date '+%F %T')"
echo "主機： $(hostname)"

sec "1. 版本與服務"
if [ -f "$APP/backend/version.json" ]; then
  echo "  版本： $(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' "$APP/backend/version.json" | head -1)"
fi
for s in webit3-api webit3-web; do
  printf "  %-14s %s" "$s" "$(systemctl is-active "$s" 2>/dev/null)"
  # 起了幾次：重啟次數異常高通常代表它一直在崩潰重啟
  n="$(systemctl show "$s" -p NRestarts --value 2>/dev/null)"
  [ -n "$n" ] && [ "$n" != "0" ] && printf "   (重啟 %s 次)" "$n"
  echo
done
# 埠號要從 install.conf 讀，不能寫死一組候選值。
# 寫死的話，改過埠的機器（例如避開衝突換成 8081）會查不到自己的服務，
# 報告上看起來像「後端沒在聽」——健檢工具自己製造假警報比不做還糟。
# （2026-07-30 實際踩到：後端在 8081，腳本只查 3000/8000/8010，誤判成服務異常。）
CONF="$DATA/install.conf"
API_PORT=""; WEB_PORT=""
[ -f "$CONF" ] && { API_PORT="$(grep -oE '^API_PORT=.*' "$CONF" | cut -d= -f2)"
                    WEB_PORT="$(grep -oE '^WEB_PORT=.*' "$CONF" | cut -d= -f2)"; }
# conf 沒有就退而從 systemd unit 反推，兩邊都沒有才用預設值
[ -n "$API_PORT" ] || API_PORT="$(grep -oE '\-\-port +[0-9]+' /etc/systemd/system/webit3-api.service 2>/dev/null | grep -oE '[0-9]+' | head -1)"
[ -n "$WEB_PORT" ] || WEB_PORT="$(grep -oE '^Environment=PORT=[0-9]+' /etc/systemd/system/webit3-web.service 2>/dev/null | grep -oE '[0-9]+' | head -1)"
API_PORT="${API_PORT:-8000}"; WEB_PORT="${WEB_PORT:-3000}"
echo "  設定的埠： 後端 $API_PORT ／ 前端 $WEB_PORT"
echo "  實際監聽："
for p in "$API_PORT" "$WEB_PORT"; do
  line="$(ss -tlnp 2>/dev/null | grep -E "[:.]${p}[[:space:]]" | head -1)"
  if [ -n "$line" ]; then
    echo "    ✓ $p  $(echo "$line" | awk '{print $4}')  $(echo "$line" | grep -oE 'users:\(\("[^"]+' | cut -d'"' -f2)"
  else
    echo "    ✗ $p  沒有任何程序在聽 ← 服務起不來或埠被改過"
  fi
done
# 另外列出「本系統以外」佔用常見埠的程序，方便判斷埠衝突的元兇
# 排除本系統自己的兩個埠，剩下的才是「別人佔著」——列出來是為了找埠衝突的元兇
other="$(ss -tlnp 2>/dev/null | grep -E '[:.](8000|8080|8443|3000|3001)[[:space:]]' \
         | grep -vE "[:.](${API_PORT}|${WEB_PORT})[[:space:]]" | head -3)"
[ -n "$other" ] && { echo "  其他程序佔用的常見埠（非本系統）："; echo "$other" | awk '{print "    " $4 "  " $NF}'; }

sec "2. 資料概況"
[ -f "$DB" ] || { echo "  !! 找不到資料庫 $DB"; exit 1; }
"$VENV/bin/python" - "$DB" "$DETAIL" <<'PY'
import sqlite3, sys, collections
db, detail = sys.argv[1], sys.argv[2] == "1"
c = sqlite3.connect(db); c.row_factory = sqlite3.Row
def one(q, *a):
    try: return c.execute(q, a).fetchone()[0]
    except Exception: return "?"

# 查詢字串先存變數：f-string 裡不能出現反斜線，直接內嵌帶引號的 SQL 會語法錯誤
Q_VC = "SELECT COUNT(*) FROM hardware WHERE asset_serial LIKE 'VC-%'"
Q_UUID = "SELECT COUNT(*) FROM hardware WHERE vm_uuid IS NOT NULL AND length(trim(vm_uuid))>0"
Q_OS = "SELECT COUNT(*) FROM hardware WHERE os IS NOT NULL AND length(trim(os))>0"
Q_NOLOC = ("SELECT COUNT(*) FROM hardware WHERE physical_location IS NULL "
           "OR length(trim(physical_location))=0")
print(f"  資產總數        {one('SELECT COUNT(*) FROM hardware')}")
print(f"  ├ RVTools 建的  {one(Q_VC)}")
print(f"  ├ 有 vm_uuid    {one(Q_UUID)}")
print(f"  ├ 有真實 OS     {one(Q_OS)}")
print(f"  └ 機房未填      {one(Q_NOLOC)}")

print()
print("  各表筆數：")
for t in ("scan_history", "scan_runs", "comparison_result", "host_account",
          "account_finding", "host_service", "source_record", "merge_review",
          "connections", "users"):
    print(f"    {t:20} {one('SELECT COUNT(*) FROM ' + t)}")

print()
print("  來源紀錄分布：")
try:
    for r in c.execute("SELECT source, COUNT(*) n FROM source_record GROUP BY source ORDER BY n DESC"):
        print(f"    {r['source']:20} {r['n']}")
except Exception:
    print("    (無)")
PY

sec "3. 重複登記"
"$VENV/bin/python" - "$DB" "$DETAIL" <<'PY'
import sqlite3, sys
db, detail = sys.argv[1], sys.argv[2] == "1"
c = sqlite3.connect(db); c.row_factory = sqlite3.Row
rows = c.execute("""
    SELECT lower(trim(hostname)) h, trim(ip) i, COUNT(*) n FROM hardware
    WHERE hostname IS NOT NULL AND length(trim(hostname))>0
      AND ip IS NOT NULL AND length(trim(ip))>0
    GROUP BY h, i HAVING n > 1 ORDER BY n DESC
""").fetchall()
print(f"  同主機名＋同 IP 的重複組數： {len(rows)}")
print(f"  清掉可少的筆數：           {sum(r['n']-1 for r in rows)}")
if rows and detail:
    print("  前 10 組：")
    for r in rows[:10]:
        print(f"    {r['h'][:34]:36} {r['i']:16} {r['n']} 筆")
elif rows:
    print("  （加 --detail 可列出是哪幾台）")
PY

sec "4. 待人工審核（RVTools 判不準的）"
"$VENV/bin/python" - "$DB" "$DETAIL" <<'PY'
import sqlite3, sys, collections
db, detail = sys.argv[1], sys.argv[2] == "1"
c = sqlite3.connect(db)
try:
    reasons = [r[0] or "(無)" for r in c.execute("SELECT reason FROM merge_review")]
except Exception:
    reasons = []
print(f"  總筆數： {len(reasons)}")
if reasons:
    print("  原因分布：")
    # 去掉括號內的動態值（IP／UUID），才聚得成類
    for r, n in collections.Counter(
        x.split("（")[0].split("(")[0].strip() for x in reasons
    ).most_common(8):
        print(f"    {n:>6} 筆  {r[:58]}")
    if detail:
        print("  原文範例：")
        for x in reasons[:3]:
            print(f"    {x[:110]}")
PY

sec "5. 掃描狀態"
"$VENV/bin/python" - "$DB" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1]); c.row_factory = sqlite3.Row
for k in ("scan_enabled", "scan_mode", "scan_time", "scan_interval_hours"):
    v = c.execute("SELECT value FROM app_settings WHERE key=?", (k,)).fetchone()
    print(f"  {k:22} {v['value'] if v else '(未設)'}")
r = c.execute("SELECT MAX(scan_time) t FROM scan_history").fetchone()
print(f"  最後掃描時間           {r['t'] if r and r['t'] else '(沒掃過)'}")
print("  掃描目標（連線設定）：")
for row in c.execute("SELECT name, connection_type, target, enabled FROM connections ORDER BY id"):
    flag = "啟用" if row["enabled"] else "停用"
    print(f"    [{flag}] {row['connection_type'] or '?':14} {row['target']}")
PY
echo "  資產 IP 推導出的網段數（應掃而未設定的看這裡）："
"$VENV/bin/python" - "$DB" <<'PY'
import sqlite3, sys, collections
c = sqlite3.connect(sys.argv[1])
segs = collections.Counter()
for (ip,) in c.execute("SELECT ip FROM hardware WHERE ip IS NOT NULL AND length(trim(ip))>0"):
    p = ip.strip().split(".")
    if len(p) == 4 and all(x.isdigit() for x in p):
        segs[".".join(p[:3]) + ".0/24"] += 1
existing = {(r[0] or "").strip() for r in c.execute("SELECT target FROM connections")}
missing = [s for s in segs if s not in existing]
print(f"    推導出 {len(segs)} 個，其中 {len(missing)} 個尚未設定掃描")
PY

sec "6. 近期錯誤（服務日誌）"
for s in webit3-api webit3-web; do
  # grep -c 沒命中時本來就會印 0，只是 exit 1；再接 || echo 0 會變成印兩次
  n=$(journalctl -u "$s" --since "24 hours ago" --no-pager 2>/dev/null | grep -ciE "error|traceback|exception")
  echo "  $s 近 24 小時錯誤行數： $n"
  [ "$n" != "0" ] && journalctl -u "$s" --since "24 hours ago" --no-pager 2>/dev/null \
    | grep -iE "error|traceback|exception" | tail -3 | sed 's/^/      /'
done

sec "7. 磁碟與備份"
df -h "$DATA" 2>/dev/null | tail -1 | awk '{print "  資料目錄可用： " $4 " (" $5 " 已用)"}'
echo "  DB 大小： $(du -h "$DB" 2>/dev/null | cut -f1)"
ls -1t "$DATA"/backups/*.db 2>/dev/null | head -3 | sed 's|.*/|  最近備份： |' || echo "  最近備份： (沒有)"

echo
echo "════════════════════════════════════════════════"
echo "報告已存： $LOG"
echo "把整份貼回給 AI 即可，不必再逐條複製指令。"
echo "需要主機名／IP 等明細時： sudo bash diagnose.sh --detail"
echo "════════════════════════════════════════════════"
