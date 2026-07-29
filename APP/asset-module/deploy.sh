#!/usr/bin/env bash
# 資產盤點模組 — .221 原生部署腳本（可重現，冪等）
# 用法：在 YOUR_SERVER_IP 上以 root 執行： bash /opt/webit3/app/deploy.sh
# 前提：程式碼已放到 /opt/webit3/app/{backend,frontend}；runtime 已裝（Python3.11 + Node20）。
# 決策依據：D34（app/ 與 data/ 分離）、D8（本機帳號）、D6/D7（備份/清除）。長官指示：不用容器/CICD，走原生。
set -euo pipefail

# 這些可由引導腳本 setup.sh（或手動 export）覆蓋；沒設就用預設（家裡 221 直接跑不受影響）。
APP="${WEBIT_APP:-/opt/webit3/app}"
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
# git_commit 由 build 機器帶進來（.221 無 git repo）：部署前 export GIT_COMMIT=$(git rev-parse --short HEAD)
printf '{"git_commit":"%s","built_at":"%s"}\n' "${GIT_COMMIT:-n/a}" "$(date '+%Y-%m-%d %H:%M')" \
  > "$APP/backend/build_info.json"

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

echo
echo "部署完成。前端： ${WEB_ORIGIN}   後端： ${API_BASE}"
echo "⚠️ 尚未建立管理員帳號，請手動執行（互動輸入密碼）："
echo "    cd ${APP}/backend && ASSET_DB_PATH=${DATA}/asset.db ${VENV}/bin/python seed_admin.py admin"
