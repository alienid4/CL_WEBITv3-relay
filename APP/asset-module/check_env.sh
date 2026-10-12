#!/usr/bin/env bash
# 戰情室資產盤點 — 部署前環境探測
# ---------------------------------------------------------------
# 在「還沒安裝」的目標機器上先跑這支，確認環境過不過關，避免 setup.sh 跑到一半才卡住。
# 純唯讀：不裝東西、不改設定、不需要 root —— 可以安心在別人的正式機上跑。
#
#   用法：   bash check_env.sh
#   結果：   畫面會印，同時存一份到 /tmp/webit3_env_check_<時間>.txt
#   有紅燈： 把那份檔整份貼回給 AI，會告訴你缺什麼、怎麼補
# ---------------------------------------------------------------
# 為什麼需要這支（都是實際會卡住部署的事）：
#   1. deploy.sh 會 pip install（PyPI）+ npm install（748 個套件）。公司內網常擋外網，
#      而且是卡在部署「中途」才爆，比事前知道難處理得多。
#   2. RHEL/Rocky 9 預設 SELinux enforcing，但 deploy.sh 完全沒處理 SELinux。
#      服務裝在非標準路徑 /opt/webit3，context 不對時 systemd 起不來，
#      而錯誤訊息不會直說是 SELinux —— 很容易誤判成程式有問題。
#   3. 3000/8000 若已被別的服務佔用，要先喬好，不是硬裝。
#
# 不用 set -e：這是探測腳本，要把所有項目跑完才有完整報告，不能中途停。
set -uo pipefail

TS="$(date +%Y%m%d_%H%M%S)"
OUT="/tmp/webit3_env_check_${TS}.txt"
exec > >(tee "$OUT") 2>&1

FAIL=0
WARN=0

ok()   { echo "  [OK]   $*"; }
warn() { echo "  [注意] $*"; WARN=$((WARN+1)); }
bad()  { echo "  [缺]   $*"; FAIL=$((FAIL+1)); }
sec()  { echo; echo "===== $* ====="; }

echo "戰情室 · 部署前環境探測"
echo "時間　： $(date '+%F %T')"
echo "主機　： $(hostname)"
echo "報告　： $OUT"

# ===== 作業系統 =====
sec "作業系統"
if [ -r /etc/os-release ]; then
  . /etc/os-release
  echo "  ${PRETTY_NAME:-未知}"
  MAJOR="$(echo "${VERSION_ID:-0}" | cut -d. -f1)"
  case "${ID:-}" in
    rhel|rocky|almalinux|centos)
      [ "${MAJOR:-0}" -ge 9 ] && ok "RHEL 系 ${MAJOR} —— setup.sh 的 dnf 提示直接適用" \
                              || warn "RHEL 系 ${MAJOR}：python3.11 與 Node 20 需額外 repo"
      ;;
    ubuntu|debian) warn "Debian 系：setup.sh 的安裝提示寫的是 dnf，改用 apt" ;;
    *)             warn "未預期的發行版 ${ID:-?}，套件指令請自行對應" ;;
  esac
else
  warn "讀不到 /etc/os-release"
fi
echo "  核心： $(uname -r)"

# ===== SELinux（deploy.sh 沒處理，最容易誤判的一項）=====
sec "SELinux"
if command -v getenforce >/dev/null 2>&1; then
  MODE="$(getenforce 2>/dev/null)"
  echo "  目前模式： $MODE"
  if [ "$MODE" = "Enforcing" ]; then
    warn "enforcing —— deploy.sh 沒有處理 SELinux。裝完若 systemd 服務起不來，"
    echo "         八成是這個，先試： sudo restorecon -Rv /opt/webit3"
    echo "         仍失敗就看： sudo ausearch -m avc -ts recent"
  else
    ok "非 enforcing，不會擋部署"
  fi
else
  ok "未安裝 SELinux 工具，應無此問題"
fi

# ===== 前置套件 =====
sec "前置套件（setup.sh 步驟 2 會擋）"
if command -v python3.11 >/dev/null 2>&1; then
  ok "python3.11 － $(python3.11 --version 2>&1)"
else
  bad "python3.11 － 補裝： sudo dnf install -y python3.11"
fi

if command -v node >/dev/null 2>&1; then
  NV="$(node --version 2>/dev/null)"
  NMAJ="$(echo "$NV" | sed 's/^v//; s/\..*//')"
  if [ "${NMAJ:-0}" -ge 20 ]; then
    ok "node － $NV"
  else
    bad "node － 目前 $NV，需要 >= 20。見 https://github.com/nodesource/distributions"
  fi
else
  bad "node － 需要 v20 以上。見 https://github.com/nodesource/distributions"
fi

command -v git >/dev/null 2>&1 && ok "git － $(git --version 2>&1)" \
                               || bad "git － 補裝： sudo dnf install -y git"

# ===== 外網連通（部署中途才爆最難處理）=====
sec "外網連通（pip / npm 要用）"
probe() {  # probe <網址> <說明> <卡住的後果>
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "$1" 2>/dev/null)"
  if [ "${code:-000}" != "000" ] && [ "${code:0:1}" != "5" ]; then
    ok "$2 可連（HTTP $code）"
  else
    bad "$2 連不到 —— $3"
  fi
}
probe "https://pypi.org/simple/"     "PyPI       " "deploy.sh 的 pip install 會失敗"
probe "https://registry.npmjs.org/"  "npm registry" "npm install（748 套件）會失敗"
probe "https://github.com/"          "GitHub     " "無法直接 git clone relay，需改用 PC 傳檔"

echo "  --- proxy 環境變數 ---"
if env | grep -i '_proxy=' >/dev/null 2>&1; then
  env | grep -i '_proxy=' | sed 's/^/    /'
  warn "偵測到 proxy 設定：sudo 執行時環境變數可能不會帶過去（sudo -E 或設 /etc/environment）"
else
  echo "    (無)"
fi

# ===== 連接埠 =====
sec "連接埠 3000 / 8000"
if command -v ss >/dev/null 2>&1; then
  for p in 3000 8000; do
    if ss -tln 2>/dev/null | grep -qE "[:.]${p}[[:space:]]"; then
      # 不算 FAIL：重裝／升級時本系統自己就佔著這兩個埠，那是正常的。
      # 只有「全新機器卻已被佔用」才要處理，所以交給人判斷是誰佔的。
      warn "port $p 已被佔用 —— 若是本系統既有服務（重裝情境）屬正常；"
      echo "         全新機器請先查是誰： sudo ss -tlnp | grep :$p"
    else
      ok "port $p 空著"
    fi
  done
else
  warn "無 ss 指令，跳過連接埠檢查"
fi

echo "  --- firewalld ---"
if systemctl is-active --quiet firewalld 2>/dev/null; then
  ok "firewalld 啟用中 —— deploy.sh 會自動開 3000/8000"
else
  echo "    未啟用（deploy.sh 會略過開埠）"
  warn "若公司另有外部防火牆／資安設備，仍要另外申請放行 3000、8000"
fi

# ===== 磁碟 =====
sec "磁碟空間"
TARGET=/opt; [ -d /opt ] || TARGET=/
AVAIL_K="$(df -Pk "$TARGET" 2>/dev/null | awk 'NR==2{print $4}')"
if [ -n "${AVAIL_K:-}" ]; then
  AVAIL_G=$(( AVAIL_K / 1024 / 1024 ))
  echo "  $TARGET 可用： ${AVAIL_G} GB"
  # venv + node_modules(748 套件) + build 產出 + DB，抓 3GB 才安全
  [ "$AVAIL_G" -ge 3 ] && ok "空間足夠（建議 >= 3GB）" || bad "空間不足，建議至少 3GB（venv + node_modules 很吃空間）"
else
  warn "讀不到磁碟資訊"
fi

# ===== 服務帳號 =====
sec "服務帳號"
if id sysctl >/dev/null 2>&1; then
  ok "sysctl 已存在（setup.sh 會沿用）"
else
  echo "    sysctl 不存在 —— setup.sh 會自動建立，或你可指定公司既有帳號"
fi

# ===== 結論 =====
echo
echo "════════════════════════════════════════════════════════════"
if [ "$FAIL" -gt 0 ]; then
  echo "結論：❌ 有 $FAIL 項缺漏、$WARN 項要注意 —— 現在跑 setup.sh 會失敗"
  echo "      先補齊上面標 [缺] 的項目，再重跑這支確認。"
elif [ "$WARN" -gt 0 ]; then
  echo "結論：⚠️  必要條件都過，但有 $WARN 項要注意（看上面 [注意]）"
  echo "      可以跑 setup.sh，但留意那幾點，出問題時先從它們查。"
else
  echo "結論：✅ 全部通過，可以直接跑： sudo bash setup.sh"
fi
echo
echo "這份報告： $OUT"
echo "有卡關就把它整份貼回給 AI。"
echo "════════════════════════════════════════════════════════════"
