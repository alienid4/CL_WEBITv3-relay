#!/usr/bin/env bash
# 資產盤點模組 — .221 原生部署腳本（可重現，冪等）
# 用法：在目標主機上以 root 執行： bash <程式所在目錄>/deploy.sh
# 前提：程式碼已放好（backend/ 與 frontend/ 在本腳本旁邊）；runtime 已裝（Python3.11 + Node20）。
# 決策依據：D34（app/ 與 data/ 分離）、D8（本機帳號）、D6/D7（備份/清除）。長官指示：不用容器/CICD，走原生。
set -euo pipefail

# 這些可由引導腳本 setup.sh（或手動 export）覆蓋；沒設就用預設（家裡 221 直接跑不受影響）。
#
# APP 預設改成「這支腳本自己所在的目錄」，不再寫死 /opt/webit3/app。
# 2026-08-25 查證踩到：221 是 git clone 到 /opt/webit3/src/APP/asset-module，
# **根本沒有 /opt/webit3/app 這個目錄**——照原本的預設跑下去會建出空目錄、
# 把 systemd unit 指到沒有程式的地方，服務直接起不來。
# 而這支腳本本來就跟程式放在一起，所以「自己在哪就部署哪」永遠是對的，
# 也不必每台機器各記一組環境變數（記不住就會有人繞過腳本自己手打指令，
# 那正是 2026-08-20~21 十幾次部署都漏掉 stamp 的原因）。
_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="${WEBIT_APP:-$_SELF_DIR}"
DATA="${WEBIT_DATA:-/opt/webit3/data}"
VENV="${WEBIT_VENV:-/opt/webit3/venv}"
SVC_USER="${SVC_USER:-sysctl}"
API_HOST="${API_HOST:-YOUR_SERVER_IP}"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"
API_BASE="http://${API_HOST}:${API_PORT}"
WEB_ORIGIN="http://${API_HOST}:${WEB_PORT}"

echo "===== [1/7] 目錄與資料夾 ====="
mkdir -p "$DATA" "$DATA/logs" "$DATA/backups"

echo "===== [2/7] 後端 venv + 相依 ====="
if [ ! -x "$VENV/bin/python" ]; then
  # PYTHON311 由 setup.sh 帶進來（隔離環境會指向隨包的可攜式 Python）；沒設就用系統的。
  "${PYTHON311:-python3.11}" -m venv "$VENV"
fi
# 離線包情境：$APP/wheels 存在就代表這是離線包，改從本地 wheel 裝、完全不連 PyPI。
# 為什麼要這樣：公司主機常常整台不能上網（2026-07-28 公司 198-014 就是），
# 一連 PyPI 就卡在這一步，而且是「部署到一半」才爆，比事前準備難處理得多。
if [ -d "$APP/wheels" ]; then
  echo "  偵測到 wheels/ → 離線安裝（不連 PyPI）"
  "$VENV/bin/python" -m pip install --no-index --find-links="$APP/wheels" -r "$APP/backend/requirements.txt"
else
  "$VENV/bin/python" -m pip install --upgrade pip >/dev/null
  "$VENV/bin/python" -m pip install -r "$APP/backend/requirements.txt"
fi

echo "===== [3/7] 初始化 DB（冪等，schema 用 CREATE IF NOT EXISTS）====="
ASSET_DB_PATH="$DATA/asset.db" "$VENV/bin/python" "$APP/backend/db.py"

echo "===== [3b/7] 收集金鑰（冪等：已存在就不動）====="
# 2026-08-16 公司主機發現：**整個專案從來沒有任何地方會產生這把金鑰**。
# 221 上那把是當初手動建的，所以家裡一直看不出問題；公司主機一按「一鍵納管」
# 就死在「讀不到收集端公鑰」。而且當時的錯誤訊息還寫著「deploy.sh 會建立」——
# 那句話是錯的，等於叫人去跑一個不會解決問題的指令。現在讓它變成真的。
#
# 私鑰永遠只留在收集器這台；公鑰才是要佈到各目標主機 authorized_keys 的東西。
COLLECTOR_KEY="$(dirname "$DATA")/.collector_key"
if [ ! -f "$COLLECTOR_KEY" ]; then
  ssh-keygen -t ed25519 -N '' -C "webit3 collector" -f "$COLLECTOR_KEY" >/dev/null
  echo "  已產生收集金鑰：$COLLECTOR_KEY"
else
  echo "  收集金鑰已存在，不動它（重新產生會讓所有已納管主機當場失聯）"
fi
chown "$SVC_USER":"$SVC_USER" "$COLLECTOR_KEY" "$COLLECTOR_KEY.pub" 2>/dev/null || true
chmod 600 "$COLLECTOR_KEY"; chmod 644 "$COLLECTOR_KEY.pub"

echo "===== [4/7] 前端 build（Node20）====="
cd "$APP/frontend"
# 離線包情境：帶了預先 build 好的 .output 但沒有 node_modules —— 直接沿用，不 build。
# 敢這樣做的兩個理由：
#   1. Nuxt3 的 .output 是自包含的，執行只需要 node，不需要 node_modules。
#   2. apiBase 走 runtimeConfig.public，執行時由 NUXT_PUBLIC_API_BASE 覆蓋（見下面的
#      systemd unit），所以「換一台機器、換一個 IP」不需要重新 build。
# 221 就地開發時 node_modules 與 .output 都在，會走 else 正常 build，行為不變。
if [ -d .output ] && [ ! -d node_modules ]; then
  echo "  偵測到預先 build 的 .output 且無 node_modules → 沿用（離線模式，不 build）"
else
  npm install --no-audit --no-fund
  NUXT_PUBLIC_API_BASE="$API_BASE" npm run build
fi

echo "===== [4.5/7] stamp 版本建置資訊（/api/version 用，讓畫面看得出換版成功）====="
# git_commit 優先自己從 repo 取——221 現在是 git clone（原本的註解假設「無 git repo」
# 已經不成立）。取不到才退回外部帶進來的 GIT_COMMIT（離線包/公司主機那種情境）。
#
# 2026-08-25 踩到：這一步漏掉的後果是 /api/version 回報一個**過期的 commit**，
# 而畫面上它看起來跟版號一樣確定。當時 221 顯示 `1d8464f`、實際 HEAD 是 `a6d7467`，
# 差 74 個 commit——拿它去比對排查會整個查錯方向。
if [ -z "${GIT_COMMIT:-}" ] && git -C "$APP" rev-parse --short HEAD >/dev/null 2>&1; then
  GIT_COMMIT="$(git -C "$APP" rev-parse --short HEAD)"
fi
printf '{"git_commit":"%s","built_at":"%s"}\n' "${GIT_COMMIT:-n/a}" "$(date '+%Y-%m-%d %H:%M')" \
  > "$APP/backend/build_info.json"
echo "  stamp: ${GIT_COMMIT:-n/a}"

echo "===== [5/7] systemd services + timers ====="
cat > /etc/systemd/system/webit3-api.service <<UNIT
[Unit]
Description=資產盤點模組 後端 API (FastAPI/uvicorn)
After=network.target

[Service]
Type=simple
User=${SVC_USER}
Group=${SVC_USER}
WorkingDirectory=${APP}/backend
Environment=ASSET_DB_PATH=${DATA}/asset.db
Environment=ASSET_API_CORS_ORIGINS=${WEB_ORIGIN}
Environment=ASSET_SCHEDULER=1
# 收集器自己的位址。納管腳本會把它寫進目標主機 authorized_keys 的 from=（來源限制），
# 也是 Push Agent 回報的目的地——**一定要是這台的真實位址**。
# 沒設過的後果（2026-08-16 在公司主機發現）：程式退回原始碼裡的預設值，而 patch 走
# 去識別化管道送出去時那個預設值被換成佔位字串，於是 from="YOUR_SERVER_IP" 被寫進
# 目標主機，sshd 永遠比對不到 → 金鑰被拒 → 腳本印「完成」、畫面顯示已納管，
# 但收集永遠連不進去。最難查的那種安靜故障，所以這裡明確帶進 unit。
Environment=ASSET_COLLECTOR_IP=${API_HOST}
ExecStart=${VENV}/bin/uvicorn api:app --host 0.0.0.0 --port ${API_PORT}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/webit3-web.service <<UNIT
[Unit]
Description=資產盤點模組 前端 (Nuxt3 node server)
After=network.target webit3-api.service

[Service]
Type=simple
User=${SVC_USER}
Group=${SVC_USER}
WorkingDirectory=${APP}/frontend
Environment=HOST=0.0.0.0
Environment=PORT=${WEB_PORT}
Environment=NUXT_PUBLIC_API_BASE=${API_BASE}
ExecStart=${NODE_BIN:-/usr/bin/node} ${APP}/frontend/.output/server/index.mjs
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

# S12：每日備份（保留7天，邏輯在 backup.py）
cat > /etc/systemd/system/webit3-backup.service <<UNIT
[Unit]
Description=資產盤點模組 每日備份 (D6 保留7天)

[Service]
Type=oneshot
User=${SVC_USER}
Group=${SVC_USER}
WorkingDirectory=${APP}/backend
Environment=ASSET_DB_PATH=${DATA}/asset.db
ExecStart=${VENV}/bin/python ${APP}/backend/backup.py
UNIT

cat > /etc/systemd/system/webit3-backup.timer <<UNIT
[Unit]
Description=每日觸發資產盤點備份

[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT

# S12：掃描紀錄清除（保留90天，邏輯在 cleanup.py）
cat > /etc/systemd/system/webit3-cleanup.service <<UNIT
[Unit]
Description=資產盤點模組 掃描紀錄清除 (D7 保留90天)

[Service]
Type=oneshot
User=${SVC_USER}
Group=${SVC_USER}
WorkingDirectory=${APP}/backend
Environment=ASSET_DB_PATH=${DATA}/asset.db
ExecStart=${VENV}/bin/python ${APP}/backend/cleanup.py
UNIT

cat > /etc/systemd/system/webit3-cleanup.timer <<UNIT
[Unit]
Description=每日觸發掃描紀錄清除

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT

# 註：真實網段掃描的排程改由「App 內建排程器」處理（ASSET_SCHEDULER=1，讀 app_settings，
# UI 可改頻率/時間、可暫停），不再用 systemd timer——才能讓使用者在畫面上調、不用碰主機。

echo "===== [6/7] 權限（服務以 ${SVC_USER} 執行）+ 防火牆 ====="
chown -R "${SVC_USER}:${SVC_USER}" /opt/webit3
if systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-port=${API_PORT}/tcp
  firewall-cmd --permanent --add-port=${WEB_PORT}/tcp
  firewall-cmd --reload
  echo "firewalld: 已開 ${API_PORT}/${WEB_PORT}"
else
  echo "firewalld 未啟用，略過開埠"
fi

echo "===== [7/7] 啟用並重啟 ====="
systemctl daemon-reload
# ⚠️ 這裡一定要 restart，不能只用 `enable --now`：
# `--now` 只在服務「沒在跑」時才啟動，對已經在跑的服務不做任何事。
# 換版時服務本來就是 active，於是新程式碼根本沒被載入——但 /api/version 是每次請求
# 即時讀 version.json/build_info.json，畫面上版號照樣跳到新版，看起來像部署成功。
# （2026-07-18 實際踩到：版號顯示 0.6.0，實際跑的還是 0.4.1 的程式，
#   新端點 404、未登入仍可讀資料，靠 systemctl show ActiveEnterTimestamp 才抓到。）
systemctl enable webit3-api.service webit3-web.service
systemctl restart webit3-api.service webit3-web.service
systemctl enable --now webit3-backup.timer
systemctl enable --now webit3-cleanup.timer

echo "===== [7.5/7] 換版驗證（版號會騙人，這裡驗「跑的是不是新碼」）====="
sleep 4
for svc in webit3-api webit3-web; do
  if ! systemctl is-active --quiet "$svc"; then
    echo "!! $svc 沒起來"
    systemctl status "$svc" --no-pager -l | tail -20
    # systemctl status 只給退出碼，真正的錯誤（Python traceback、Permission denied、
    # Address already in use）都在 journal 裡。不印出來的話，log 貼回來也查不出原因。
    echo "--- journalctl（真正的錯誤通常在這裡） ---"
    journalctl -u "$svc" -n 40 --no-pager 2>/dev/null | tail -30 || echo "（取不到 journal）"
    echo "--- 以服務帳號實際載入一次，看是不是 import 失敗 ---"
    sudo -u "${SVC_USER}" ASSET_DB_PATH="${DATA}/asset.db" \
      "$VENV/bin/python" -c "import sys; sys.path.insert(0,'${APP}/backend'); import api" 2>&1 | tail -15 || true
    echo "--- SELinux 模式（enforcing 時常是元凶） ---"
    getenforce 2>/dev/null || echo "（無 SELinux）"
    exit 1
  fi
  echo "$svc: active（啟動於 $(systemctl show "$svc" -p ActiveEnterTimestamp --value)）"
done

# 上面那圈只證明「服務起得來」，**不等於「跑的是新碼」**——標題寫「驗跑的是不是新碼」
# 但實際上沒有驗，這一段補上真正的比對（2026-08-25 發現這個落差）。
#
# 三個值必須一致：repo 的 HEAD、stamp 檔記的、以及 API 實際回報的。
# 任兩個不一致就代表有一步沒生效：
#   HEAD ≠ stamp   → [4.5] 沒跑到（就是 2026-08-20~21 那十幾次手動部署的情況）
#   stamp ≠ API    → 服務沒真的重啟，還是舊的行程在跑（2026-07-18 踩過）
echo "===== [7.6/7] 驗證跑的確實是這份 commit ====="
_head="$(git -C "$APP" rev-parse --short HEAD 2>/dev/null || echo n/a)"
_stamp="$(sed -n 's/.*"git_commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
          "$APP/backend/build_info.json" 2>/dev/null || echo n/a)"
_api="$(curl -s --max-time 10 "${API_BASE}/api/version" 2>/dev/null \
        | sed -n 's/.*"git_commit"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' || echo n/a)"
echo "  repo HEAD = ${_head}"
echo "  stamp     = ${_stamp}"
echo "  API 回報   = ${_api}"
if [ "$_head" != "n/a" ] && { [ "$_head" != "$_stamp" ] || [ "$_stamp" != "$_api" ]; }; then
  echo "!! 三者不一致——畫面上的版本資訊會騙人，請查上面哪一步沒生效"
  exit 1
fi
echo "  ✓ 一致"

echo
echo "部署完成。前端： ${WEB_ORIGIN}   後端： ${API_BASE}"

# 這句原本是**無條件印**的，於是每次部署都喊「尚未建立管理員帳號」——
# 而 221 上的 admin 2026-07-29 就建好了，使用者一直用它在匯資料。
# 2026-08-26 使用者問「你是指 root 嗎」才發現這是假警報，而且它連問了兩天。
#
# 假警報比沒有警報更糟：喊久了真的沒帳號時也不會有人當一回事。
# 改成真的去查 users 表，沒有才印。
_admin_n="$("$VENV/bin/python" - "$DATA/asset.db" <<'PY' 2>/dev/null || echo -1
import sqlite3, sys
try:
    print(sqlite3.connect(sys.argv[1]).execute("SELECT COUNT(*) FROM users").fetchone()[0])
except Exception:
    print(-1)
PY
)"
if [ "$_admin_n" = "0" ]; then
  echo "⚠️ 尚未建立管理員帳號，請手動執行（互動輸入密碼）："
  echo "    cd ${APP}/backend && ASSET_DB_PATH=${DATA}/asset.db ${VENV}/bin/python seed_admin.py admin"
elif [ "$_admin_n" = "-1" ]; then
  echo "（查不到 users 表，無法判斷有沒有管理員帳號——不臆測，請自行確認）"
else
  echo "登入帳號：已有 ${_admin_n} 個"
fi
