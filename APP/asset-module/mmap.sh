#!/usr/bin/env bash
# 網段掃描 · 一鍵安裝
# ---------------------------------------------------------------
# 在「網路走得通的那台 Linux」上用 root 執行一次，之後每天深夜自動掃，
# 你只要到 log 目錄看結果、掃完後打包上傳。
#
#   用法：   sudo bash mmap.sh
#   移除：   sudo bash mmap.sh --uninstall
#
# 這支會做四件事，全部冪等（重跑不會壞、不會重複設定）：
#   1. 建工作目錄 /opt/webit3-scan
#   2. 放進掃描器與網段清單
#   3. 寫 /etc/cron.d/webit3-scan（每天 22:00 開始，06:00 自動收工）
#   4. 驗證設定並印出「怎麼確認它在跑」
#
# 不需要 nmap、不需要安裝任何套件——掃描器只用 Python 標準庫。
# 不需要 root 以外的權限，也不會改動系統其他設定。
# ---------------------------------------------------------------
set -uo pipefail

WORK="${WEBIT_SCAN_HOME:-/opt/webit3-scan}"
CRON_FILE=/etc/cron.d/webit3-scan
CRON_HOUR="${SCAN_START_HOUR:-22}"      # 幾點開始（預設 22:00）
WINDOW="${SCAN_WINDOW:-22:00-06:00}"    # 允許掃描的時段
# 探測埠。預設是通用值，但實際掃得到什麼取決於防火牆放行了哪些——
# 拿已知不通的埠去掃，除了浪費時間（每個位址多一個 timeout），還會讓
# 「無回應」的原因混進防火牆因素。放行清單跟預設不同時用這個覆蓋：
#   SCAN_PORTS=22,445 sudo bash mmap.sh
PORTS="${SCAN_PORTS:-22,3389,445,443}"
HERE="$(cd "$(dirname "$0")" && pwd)"

say() { echo "  $*"; }
sec() { echo; echo "===== $* ====="; }

# ===== 移除 =====
if [ "${1:-}" = "--uninstall" ]; then
  echo "移除網段掃描設定"
  [ -f "$CRON_FILE" ] && { rm -f "$CRON_FILE"; say "✓ 已移除 $CRON_FILE"; } \
                      || say "· $CRON_FILE 本來就不存在"
  say "· 工作目錄 $WORK 保留（裡面有掃描結果，要刪請自行 rm -rf）"
  echo "完成。"
  exit 0
fi

echo "════════════════════════════════════════════════"
echo " 網段掃描 · 一鍵安裝"
echo " 主機： $(hostname)   時間： $(date '+%F %T')"
echo "════════════════════════════════════════════════"

# ===== 1. 前置檢查 =====
sec "1. 前置檢查"
[ "$(id -u)" = "0" ] || { echo "!! 請用 root 執行： sudo bash mmap.sh"; exit 1; }
say "✓ 以 root 執行"

if command -v python3 >/dev/null 2>&1; then
  say "✓ python3 － $(python3 --version 2>&1)"
else
  echo "!! 找不到 python3。掃描器需要 Python 3.6 以上（RHEL/Rocky 內建即可）"
  exit 1
fi

# 掃描器本體跟這支放在一起（隨 patch 一起送過來）
SCANNER_SRC=""
for cand in "$HERE/scan_segments.py" "$WORK/scan_segments.py"; do
  [ -f "$cand" ] && { SCANNER_SRC="$cand"; break; }
done
if [ -z "$SCANNER_SRC" ]; then
  echo "!! 找不到 scan_segments.py"
  echo "   它應該跟這支腳本放在同一個目錄。可從這裡取得："
  echo "   https://github.com/alienid4/CL_WEBITv3-relay/blob/main/tools/scan_segments.py"
  exit 1
fi
say "✓ 掃描器： $SCANNER_SRC"

# 網段清單：掃描機連不到戰情室的資料庫，所以這份一定要人先放進來。
# 找不到就明確講怎麼取得，不要預設一份空清單然後「成功安裝」——
# 那會變成 cron 每天準時跑、卻什麼都沒掃，而且沒人知道。
SEG_SRC=""
for cand in "$HERE/segments.txt" "$WORK/segments.txt"; do
  [ -f "$cand" ] && { SEG_SRC="$cand"; break; }
done
if [ -z "$SEG_SRC" ]; then
  echo
  echo "!! 找不到 segments.txt（要掃哪些網段）"
  echo
  echo "   請在戰情室主機上用瀏覽器開這個網址，另存成 segments.txt："
  echo "     http://<戰情室IP>:<埠>/api/connections/suggest-segments?fmt=txt"
  echo
  echo "   然後把它放到這支腳本旁邊（$HERE/），再執行一次。"
  exit 1
fi
SEG_COUNT="$(grep -cvE '^\s*(#|$)' "$SEG_SRC" 2>/dev/null || echo 0)"
say "✓ 網段清單： $SEG_SRC（$SEG_COUNT 個網段）"
if [ "$SEG_COUNT" = "0" ]; then
  echo "!! 網段清單裡沒有任何有效網段，請確認檔案內容"
  exit 1
fi

# ===== 2. 建目錄與放檔 =====
sec "2. 建立工作目錄 $WORK"
mkdir -p "$WORK/out"
cp -f "$SCANNER_SRC" "$WORK/scan_segments.py"
# 網段清單只在「還沒有」時複製，避免覆蓋掉現場已經調整過的版本
if [ ! -f "$WORK/segments.txt" ] || ! cmp -s "$SEG_SRC" "$WORK/segments.txt"; then
  if [ -f "$WORK/segments.txt" ]; then
    cp -f "$WORK/segments.txt" "$WORK/segments.txt.bak.$(date +%Y%m%d_%H%M%S)"
    say "· 既有 segments.txt 已備份"
  fi
  cp -f "$SEG_SRC" "$WORK/segments.txt"
fi
chmod 755 "$WORK"
chmod 644 "$WORK/scan_segments.py" "$WORK/segments.txt"
say "✓ 掃描器　： $WORK/scan_segments.py"
say "✓ 網段清單： $WORK/segments.txt"
say "✓ 結果目錄： $WORK/out"

# ===== 3. 設定 cron =====
sec "3. 設定每日排程"
# 用 /etc/cron.d/ 專屬檔案而不是改 root 的 crontab：
# 一個檔案代表一件事，重跑這支腳本是「覆寫」而不是「再加一筆」，
# 不會累積成好幾條重複排程；要移除也只是刪一個檔。
cat > "$CRON_FILE" <<CRONEOF
# 網段掃描（由 mmap.sh 產生，重跑會覆寫此檔）
# 每天 ${CRON_HOUR}:00 開始，掃描器自己會在 ${WINDOW} 之外收工並保留進度。
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 ${CRON_HOUR} * * * root cd ${WORK} && python3 scan_segments.py --segments segments.txt --out ./out --window '${WINDOW}' --ports '${PORTS}' --limit 0 >> ${WORK}/scan.log 2>&1
CRONEOF
chmod 644 "$CRON_FILE"
say "✓ 已寫入 $CRON_FILE"

# 驗證：cron 檔存在不代表 cron 服務會讀它。這裡確認服務是活的，
# 否則會出現「設定看起來都對、但永遠不會執行」這種最難查的情況。
CRON_SVC=""
for svc in crond cron; do
  systemctl list-unit-files 2>/dev/null | grep -q "^${svc}.service" && { CRON_SVC="$svc"; break; }
done
if [ -n "$CRON_SVC" ]; then
  if systemctl is-active --quiet "$CRON_SVC"; then
    say "✓ $CRON_SVC 服務執行中"
  else
    say "⚠ $CRON_SVC 服務沒在跑，排程不會被執行。啟動： systemctl enable --now $CRON_SVC"
  fi
else
  say "⚠ 找不到 cron 服務，請確認這台有安裝 cronie"
fi

# ===== 4. 試跑一個網段 =====
sec "4. 立即試跑一個網段（確認網路真的通得到）"
say "（用 --now 忽略時間窗，只掃 1 個網段，不影響正式進度）"
cd "$WORK" || exit 1
TEST_OUT="$WORK/out/_install_check"
rm -rf "$TEST_OUT"
head -n 50 segments.txt | grep -vE '^\s*(#|$)' | head -1 > /tmp/_one_seg.txt
if python3 scan_segments.py --segments /tmp/_one_seg.txt --out "$TEST_OUT" \
     --ports "$PORTS" --now --limit 1 --pause 0 2>&1 | sed 's/^/    /'; then
  FOUND="$(tail -n +2 "$TEST_OUT"/scan_results_*.csv 2>/dev/null | wc -l)"
  if [ "${FOUND:-0}" -gt 0 ]; then
    say "✓ 試跑找到 $FOUND 台，網路可達"
  else
    say "⚠ 試跑找到 0 台 —— 這個網段可能防火牆未放行"
    say "  正式跑完後請看 out/segments_status_*.csv 的 verdict 欄位，"
    say "  那裡會標明「整段無回應」與「可達」的差別，不要直接當成機器不在。"
  fi
else
  say "⚠ 試跑失敗，請看上面的錯誤訊息"
fi
rm -f /tmp/_one_seg.txt

# ===== 完成 =====
NEXT="今天 ${CRON_HOUR}:00"
[ "$(date +%H)" -ge "$CRON_HOUR" ] && NEXT="明天 ${CRON_HOUR}:00"
echo
echo "════════════════════════════════════════════════"
echo "✅ 安裝完成"
echo
echo "  排程　　： 每天 ${CRON_HOUR}:00 開始（${WINDOW} 之外自動收工）"
echo "  下次執行： ${NEXT}"
# 把實際用的埠印出來：掃不到東西時，第一個要確認的就是「探了哪些埠」，
# 不印的話只能回頭翻 cron 檔才知道這次到底掃了什麼。
echo "  探測埠　： ${PORTS}"
case ",${PORTS}," in
  *,443,*) ;;
  *) echo "            （未含 443：只開 https 的設備會掃不到，會被判成沒回應）" ;;
esac
echo "  網段總數： ${SEG_COUNT} 個（實測一個 /24 約 19 秒，全部約 1.4 小時）"
echo
echo "  工作目錄： ${WORK}"
echo "    ├ scan.log              執行紀錄（要看進度就看這個）"
echo "    ├ segments.txt          要掃的網段清單"
echo "    └ out/"
echo "        ├ scan_results_*.csv     找到的主機（含 DNS 反解）"
echo "        ├ segments_status_*.csv  每個網段可達與否 ← 上傳時不能少"
echo "        └ progress.txt           已完成的網段（隔天自動接續）"
echo
echo "  確認它有在跑："
echo "    tail -f ${WORK}/scan.log"
echo "    cat ${WORK}/out/progress.txt | wc -l    # 已完成幾個網段"
echo
echo "  全部掃完後（progress.txt 行數 = ${SEG_COUNT}）："
echo "    cd ${WORK} && python3 scan_segments.py --segments segments.txt --out ./out --package"
echo "    → 產出 zip，拿到戰情室「資料匯入 → 掃描結果匯入」上傳"
echo
echo "  要移除排程： sudo bash mmap.sh --uninstall"
echo "════════════════════════════════════════════════"
