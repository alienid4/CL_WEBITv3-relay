#!/usr/bin/env bash
# 戰情室資產盤點 — 公司正式機「引導式」安裝
# ---------------------------------------------------------------
# 給不熟指令的同事用：一步一步問、自動檢查、全程寫 log。
# 任何一步出錯就【停下來】，log 完整保留 —— 把 log 整份貼回給 AI 就能定位並修復。
#
#   用法：   sudo bash setup.sh
#   出錯了： 照畫面最後印的 log 路徑，把那份檔整份貼回給 AI（或先看 tail -50 <log>）
#   修好後： 直接再跑一次 sudo bash setup.sh（冪等，重跑不會弄壞已完成的部分）
# ---------------------------------------------------------------
# 不用 set -e：改成自己逐步判斷回傳碼，這樣 ERR trap 有機會把「錯在哪一步」寫進 log。
set -uo pipefail

# ===== log：每次一個新檔、帶時間戳、不覆蓋舊的（可回頭比對歷次安裝）=====
LOG_DIR="${WEBIT_DATA:-/opt/webit3/data}/logs"
mkdir -p "$LOG_DIR" 2>/dev/null || LOG_DIR="/tmp"   # data 目錄還沒建時先退到 /tmp
LOG="$LOG_DIR/setup_$(date +%Y%m%d_%H%M%S).log"
# 把畫面的所有輸出（含底層 deploy.sh 的）同時抄一份進 log
exec > >(tee -a "$LOG") 2>&1

STEP=0
CURRENT="(初始化)"
step() { STEP=$((STEP+1)); CURRENT="$1"; echo; echo "===== [步驟 $STEP] $CURRENT ====="; }

# 任一未預期的指令失敗會跳來這裡：記錄是哪一步、退出碼、log 位置，然後停。
on_error() {
  local code=$?
  echo
  echo "════════════════════════════════════════════════════════════"
  echo "!! 安裝中斷 —— 卡在【步驟 $STEP：$CURRENT】(退出碼 $code)"
  echo "!!"
  echo "!! 完整過程都在這份 log： $LOG"
  echo "!! 修復：把上面這個檔【整份】貼回給 AI，會告訴你哪裡錯、怎麼修。"
  echo "!!       想先自己看： tail -50 \"$LOG\""
  echo "!! 修好後重跑： sudo bash setup.sh  （冪等，安全重跑）"
  echo "════════════════════════════════════════════════════════════"
  exit "$code"
}
trap on_error ERR

echo "戰情室正式機 · 引導安裝"
echo "開始時間： $(date '+%F %T')"
echo "log 檔　： $LOG"

# ===== 步驟 1：權限 =====
step "檢查權限（需要 root）"
if [ "$(id -u)" != "0" ]; then
  echo "!! 請用 root 或 sudo 執行：  sudo bash setup.sh"
  exit 1
fi
echo "  ✓ 以 root 執行"

# ===== 步驟 2：前置環境 =====
# 系統有 python3.11 / node20 就直接用；沒有的話，若這包裡帶了可攜式版本就改用它。
# 為什麼要這樣：完全隔離的環境（不能上網、也沒有 yum repo／ISO）根本裝不了套件，
# 而 RHEL 的 python3.11 RPM 又相依到較新的 OpenSSL，硬裝有弄壞系統的風險。
# 可攜式版本是解壓即用、自帶相依（含自己的 OpenSSL），放在 /opt/webit3/runtime，
# 不寫進 /usr、不動任何系統套件，要移除直接刪目錄。
# （2026-07-29 公司 198-014：RHEL 9、無網路、無 repo，就是靠這條路裝起來的。）
step "檢查前置環境（python3.11 / node20）"
HERE="$(cd "$(dirname "$0")" && pwd)"
RUNTIME="${WEBIT_RUNTIME:-/opt/webit3/runtime}"
MISS=0

if command -v python3.11 >/dev/null 2>&1; then
  PYTHON311="$(command -v python3.11)"
  echo "  ✓ python3.11 － $(python3.11 --version 2>&1)（系統既有）"
elif [ -x "$RUNTIME/python311/bin/python3" ]; then
  # 重跑時人常常不在原本解壓的資料夾（例如直接在 /opt/webit3/app 底下跑），
  # 那裡沒有 tarball，但 runtime 上次已經解開了 —— 沿用就好，不必回頭翻原始包。
  PYTHON311="$RUNTIME/python311/bin/python3"
  echo "  ✓ python3.11 － $("$PYTHON311" --version 2>&1)（沿用先前解開的可攜式版本）"
elif [ -f "$HERE/python311-standalone.tar.gz" ]; then
  mkdir -p "$RUNTIME/python311"
  tar xzf "$HERE/python311-standalone.tar.gz" -C "$RUNTIME/python311" --strip-components=1
  # 解開來是 drwxr-x--- root:root，服務帳號（非 root）會連目錄都進不去，
  # venv/bin/python 只是指向這裡的 symlink，於是 systemd 啟動時直接 exit。
  # deploy.sh 後面雖然會 chown 整個 /opt/webit3，但 RUNTIME 可能被指到別處，
  # 這裡先放行讀取與進入，不依賴後面那次 chown。
  chmod -R a+rX "$RUNTIME/python311"
  PYTHON311="$RUNTIME/python311/bin/python3"
  if [ -x "$PYTHON311" ]; then
    echo "  ✓ python3.11 － $("$PYTHON311" --version 2>&1)（隨包可攜式，未裝進系統）"
  else
    echo "  ✗ 可攜式 Python 解開後不可執行"; MISS=1
  fi
else
  echo "  ✗ 缺 python3.11 － 系統沒有，這包裡也沒有可攜式版本"
  echo "     能上網的話： dnf install -y python3.11"
  MISS=1
fi

# 一定要先 command -v 再呼叫：node 不存在時直接執行會回 127，
# 那個退出碼會觸發本檔開頭的 ERR trap 讓安裝中斷（2>/dev/null 只擋輸出、擋不住退出碼）。
if command -v node >/dev/null 2>&1; then
  NODE_MAJOR="$(node --version | sed 's/^v//; s/\..*//')"
else
  NODE_MAJOR=0
fi
if [ "${NODE_MAJOR:-0}" -ge 20 ]; then
  NODE_BIN="$(command -v node)"
  echo "  ✓ node － $(node --version)（系統既有）"
elif [ -x "$RUNTIME/node/bin/node" ]; then
  NODE_BIN="$RUNTIME/node/bin/node"
  echo "  ✓ node － $("$NODE_BIN" --version 2>&1)（沿用先前解開的可攜式版本）"
elif ls "$HERE"/node-*-linux-x64.tar.xz >/dev/null 2>&1; then
  mkdir -p "$RUNTIME/node"
  tar xf "$HERE"/node-*-linux-x64.tar.xz -C "$RUNTIME/node" --strip-components=1
  chmod -R a+rX "$RUNTIME/node"   # 同上：前端服務也是以非 root 帳號執行
  NODE_BIN="$RUNTIME/node/bin/node"
  if [ -x "$NODE_BIN" ]; then
    echo "  ✓ node － $("$NODE_BIN" --version)（隨包可攜式，未裝進系統）"
  else
    echo "  ✗ 可攜式 Node 解開後不可執行"; MISS=1
  fi
else
  echo "  ✗ 缺 node 20 以上 － 系統沒有，這包裡也沒有可攜式版本"
  MISS=1
fi

# git 只用來標記版本（取不到就記 n/a），不是安裝的必要條件，缺了不擋。
command -v git >/dev/null 2>&1 && echo "  ✓ git － $(git --version 2>&1)" \
                               || echo "  · 無 git（不影響安裝，版本資訊會記成 n/a）"

if [ "$MISS" != "0" ]; then
  echo "!! 前置環境不齊，補齊後再跑一次（這一步不算失敗，是提醒你先準備東西）"
  exit 1
fi
export PYTHON311 NODE_BIN
echo "  ✓ 前置環境齊全"

# ===== 步驟 3：問設定 =====
step "設定這台 Server"
DEF_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
read -rp "  這台 Server 對外服務的 IP [${DEF_IP:-請輸入}]： " SERVER_IP
SERVER_IP="${SERVER_IP:-$DEF_IP}"
if [ -z "$SERVER_IP" ]; then
  echo "!! 沒給 IP，無法繼續（別台機器要用這個位址連進來）"
  exit 1
fi
read -rp "  跑服務的系統帳號 [sysctl]： " SVC
SVC="${SVC:-sysctl}"

# 連接埠先檢查有沒有被佔用。不先問清楚的話，後端會拖到部署最後一步才爆
#   [Errno 98] error while attempting to bind on address ('0.0.0.0', 8000): address already in use
# 而 systemd 設了 Restart=on-failure，會每 3 秒重試一次，看起來像程式壞掉，
# 實際上只是埠被別的服務佔著。
# （2026-07-29 公司 198-014 實際踩到，restart counter 累積到 696 次才查出來。）
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"
port_taken() { ss -tln 2>/dev/null | grep -qE "[:.]${1}[[:space:]]"; }
for _ in 1 2 3; do          # 換了埠也要再驗一次，新埠同樣可能被佔
  BUSY=""
  port_taken "$API_PORT" && BUSY="$BUSY ${API_PORT}(後端)"
  port_taken "$WEB_PORT" && BUSY="$BUSY ${WEB_PORT}(前端)"
  [ -z "$BUSY" ] && break
  echo
  echo "  ⚠ 這些埠已被佔用：$BUSY"
  ss -tlnp 2>/dev/null | grep -E "[:.](${API_PORT}|${WEB_PORT})[[:space:]]" | sed 's/^/      /'
  echo "    佔用者若是本系統既有服務（重新安裝的情況），直接 Enter 繼續即可，部署時會重啟它。"
  echo "    若是別的程式，繼續下去後端一定起不來。"
  read -rp "  要改後端埠就輸入新埠號，直接 Enter 沿用 ${API_PORT}： " NEWPORT
  [ -z "$NEWPORT" ] && break
  API_PORT="$NEWPORT"
done

echo
echo "  將會這樣設定："
echo "    前端網址： http://$SERVER_IP:$WEB_PORT   ← 同事用瀏覽器開這個"
echo "    後端 API： http://$SERVER_IP:$API_PORT"
echo "    服務帳號： $SVC"
read -rp "  以上正確就按 Enter 繼續（要取消按 Ctrl-C）： " _
# ⚠️ 提醒：IP 一定要填「別台連得到」的位址，不能填 localhost / 127.0.0.1，否則別人連不到。

# ===== 步驟 4：服務帳號 =====
step "確保服務帳號 $SVC 存在"
if id "$SVC" >/dev/null 2>&1; then
  echo "  ✓ 帳號 $SVC 已存在"
else
  useradd -r -m "$SVC"
  echo "  ✓ 已建立帳號 $SVC"
fi

# ===== 步驟 5：把程式碼搬到部署位置 =====
# 為什麼需要這一步：deploy.sh 讀的是 $WEBIT_APP（預設 /opt/webit3/app），
# 但你是在解壓/clone 出來的資料夾（例如 /tmp/asset-module）執行這支。
# 少了這一步，deploy.sh 會找不到 backend/requirements.txt 直接失敗。
# 另外程式碼放 /tmp 會被系統清掉，本來就該搬到常駐位置。
HERE="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="${WEBIT_APP:-/opt/webit3/app}"
step "安裝程式碼到 $APP_DIR"
if [ ! -f "$HERE/deploy.sh" ]; then
  echo "!! 找不到 $HERE/deploy.sh —— 請確認是在解開的資料夾裡執行"
  exit 1
fi
mkdir -p "$APP_DIR"
cp -r "$HERE/backend"  "$APP_DIR/"
cp -r "$HERE/frontend" "$APP_DIR/"
cp    "$HERE/deploy.sh" "$APP_DIR/"
echo "  ✓ backend / frontend / deploy.sh 已就位"

# 離線包（主機不能上網時使用）：有就帶過去，deploy.sh 會自動偵測並改走離線路徑。
# wheels 用 tar.gz 收著：某些 wheel 檔名很長（manylinux 那串），
# 經 Windows 中轉時會超過 MAX_PATH 260 字元而整個檔案遺失，壓起來就沒這問題。
if [ -f "$HERE/wheels.tar.gz" ]; then
  mkdir -p "$APP_DIR/wheels"
  tar xzf "$HERE/wheels.tar.gz" -C "$APP_DIR/wheels"
  echo "  ✓ 偵測到 wheels.tar.gz（$(ls "$APP_DIR/wheels" | wc -l) 個）→ 稍後 pip 離線安裝，不連 PyPI"
elif [ -d "$HERE/wheels" ]; then
  cp -r "$HERE/wheels" "$APP_DIR/"
  echo "  ✓ 偵測到 wheels/（$(ls "$HERE/wheels" | wc -l) 個）→ 稍後 pip 離線安裝，不連 PyPI"
fi
if [ -f "$HERE/frontend-output.tar.gz" ]; then
  tar xzf "$HERE/frontend-output.tar.gz" -C "$APP_DIR/frontend/"
  echo "  ✓ 偵測到預先 build 的前端 → 已解開，稍後不需 npm install/build"
fi

# ===== 步驟 6：實際部署（交給 deploy.sh，帶入上面的設定）=====
step "部署：venv / 依賴 / 建 DB / 前端 / systemd / 防火牆"
export API_HOST="$SERVER_IP" SVC_USER="$SVC" API_PORT WEB_PORT
export GIT_COMMIT="$(git -C "$HERE" rev-parse --short HEAD 2>/dev/null || echo n/a)"
echo "  → 呼叫 deploy.sh（它的輸出也會一起寫進上面那份 log）"
bash "$APP_DIR/deploy.sh"

# ===== 步驟 6：建管理員 =====
step "建立管理員帳號（等一下會要你輸入密碼）"
DATA="${WEBIT_DATA:-/opt/webit3/data}"
VENV="${WEBIT_VENV:-/opt/webit3/venv}"
APP="${WEBIT_APP:-/opt/webit3/app}"
echo "  請設定 admin 的登入密碼："
sudo -u "$SVC" ASSET_DB_PATH="$DATA/asset.db" "$VENV/bin/python" "$APP/backend/seed_admin.py" admin

# ===== 留下安裝設定 =====
# 之後 patch.sh 要重新部署時得知道這台當初怎麼裝的（埠、帳號、對外位址、runtime 路徑）。
# 沒有這份檔就只能從 systemd unit 反推，能推但脆弱。
CONF="$DATA/install.conf"
cat > "$CONF" <<CONFEOF
API_HOST=$SERVER_IP
API_PORT=$API_PORT
WEB_PORT=$WEB_PORT
SVC_USER=$SVC
PYTHON311=${PYTHON311:-python3.11}
NODE_BIN=${NODE_BIN:-/usr/bin/node}
CONFEOF
chown "$SVC:$SVC" "$CONF" 2>/dev/null
echo "  安裝設定已留存： $CONF（之後 patch 會自動沿用）"

# ===== 完成 =====
echo
echo "════════════════════════════════════════════════════════════"
echo "✅ 安裝完成！ $(date '+%F %T')"
echo
echo "   打開瀏覽器：  http://$SERVER_IP:3000"
echo "   用剛剛設定的 admin 帳密登入。"
echo
echo "   這次的完整 log： $LOG"
echo "   想先放 demo 假資料看畫面（正式上線前記得清）："
echo "     sudo -u $SVC ASSET_DB_PATH=$DATA/asset.db $VENV/bin/python $APP/backend/seed_demo.py"
echo
echo "   若公司另有防火牆／資安設備，請再放行這台的 3000、8000 兩個埠。"
echo "════════════════════════════════════════════════════════════"
