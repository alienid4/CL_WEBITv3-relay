#!/usr/bin/env bash
# 資產盤點模組 — .221 原生部署腳本（可重現，冪等）
# 用法：在目標主機上以 root 執行： bash <程式所在目錄>/deploy.sh
# 前提：程式碼已放好（backend/ 與 frontend/ 在本腳本旁邊）；runtime 已裝（Python3.11 + Node20）。
# 決策依據：D34（app/ 與 data/ 分離）、D8（本機帳號）、D6/D7（備份/清除）。長官指示：不用容器/CICD，走原生。
set -euo pipefail

# 這些可由引導腳本 setup.sh（或手動 export）覆蓋。路徑類的沒設就用預設；
# **API_HOST 沒有預設，沒設一律失敗**（理由見下方那段）。
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

# API_HOST 刻意**沒有預設值**（2026-10-06）。
# 為什麼不給預設：這裡原本預設成開發機位址，而那個字面值就這樣被打進交付給公司正式機
# 的 app 包裡。換成別的預設值（localhost／另一個 IP）只是把問題藏起來——下一個人還是
# 會塞一個進來，而且錯的時候一樣是「靜默壞」：CORS 白名單算錯 → 畫面只寫「匯出失敗」。
# 沒設就大聲失敗，一秒就知道要補什麼（2026-09-09 公司機器為此停過一輪）。
#
# 正常路徑都會帶進來，不會踩到這裡：
#   · setup.sh → 步驟 3 問「對外服務 IP」，沒填直接 exit 1，再 export API_HOST
#   · patch.sh → 讀 $DATA/install.conf（或從既有 systemd unit 反推）後 export
# 會踩到的只有「手動直接跑 deploy.sh」，照下面訊息補一個環境變數即可。
if [ -z "${API_HOST:-}" ]; then
  echo "!! 沒有設定 API_HOST —— 這台 Server 對外服務的位址，別台機器要用它連進來。" >&2
  echo "   CORS 白名單與前端的 NUXT_PUBLIC_API_BASE 都靠它算；猜錯會變成「匯出失敗」那種看不出原因的故障。" >&2
  echo >&2
  echo "   怎麼設（三種任一）：" >&2
  echo "     1. 走引導安裝（建議）： bash setup.sh      ← 它會問你，問完自動帶進來" >&2
  echo "     2. 這次手動指定：       API_HOST=<本機對外IP> bash deploy.sh" >&2
  echo "     3. 先 export：          export API_HOST=<本機對外IP>; bash deploy.sh" >&2
  echo >&2
  echo "   本機對外位址可用這行查： hostname -I | awk '{print \$1}'" >&2
  exit 1
fi

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
# 舊版 SSH 函式庫（隔離；只給「SSH 測連線」與舊 SAN switch 用，理由見 patch.sh [4.7/5]）
LEGACY_LIB="${WEBIT_LEGACY_SSH_LIB:-/opt/webit3/legacy_ssh/lib}"
_LW="$(ls "$APP"/../../.project/tools/ssh_legacy_probe/paramiko-3.5.1-*.whl "$APP"/legacy_ssh/paramiko-3.5.1-*.whl 2>/dev/null | head -1 || true)"
if [ ! -d "$LEGACY_LIB/paramiko-3.5.1.dist-info" ] && [ -n "$_LW" ]; then
  mkdir -p "$LEGACY_LIB"
  if ( umask 022; "$VENV/bin/python" -m pip install --no-index --no-deps --target "$LEGACY_LIB" "$_LW" ) >/dev/null 2>&1; then
    chmod -R a+rX "$(dirname "$LEGACY_LIB")" || true
    echo "  ✓ 舊版 SSH 函式庫已安裝到 $LEGACY_LIB"
  else
    echo "  !! 舊版 SSH 函式庫安裝失敗（只影響 SSH 測連線的舊版測試）"
  fi
fi

echo "===== [2c/7] 部署前測試關卡（全套；方案 A：commit 只跑改到的檔，全套在這裡擋）====="
# 2026-09-10 使用者定案「方案 A」：把全套測試從 pre-commit 搬到這裡（見 CLAUDE.md
# 「測試閘門分兩段」）。commit 只跑改到的檔 → 幾秒過；全套在部署 221「之前」跑，
# 沒過就中止、不把未通過測試的碼放上正式機。這裡是天生的守門點：部署一律走這支腳本。
#
# 為什麼放在 [2] 之後 [3] 之前：測試需要 backend 相依（[2] 剛裝好），又要在動到 DB／
# build／服務「之前」失敗（fail fast，別改壞一半才發現測試紅）。測試用 tempfile DB，
# 不碰 $DATA/asset.db，正式資料安全。
_REPO_ROOT="$(cd "$APP/../.." && pwd)"   # deploy.sh 在 APP/asset-module/ 下，往上兩層是 repo 根
if [ "${WEBIT_SKIP_TESTS:-0}" = "1" ]; then
  echo "  ⚠️ WEBIT_SKIP_TESTS=1 → 略過測試關卡（你自己確認過才該用；會少一道守門）"
elif [ -d "$APP/wheels" ]; then
  echo "  離線包情境（偵測到 wheels/）→ 略過：此包在 PC／NB 出包前已跑過全套，離線機通常也沒測試環境"
elif [ ! -d "$_REPO_ROOT/tests" ]; then
  echo "  找不到 $_REPO_ROOT/tests → 略過（這裡不是完整 repo，可能是只帶 APP 的精簡佈署）"
elif "$VENV/bin/python" -c "import pytest" >/dev/null 2>&1; then
  # 平行跑測試（2026-09-17）：實測全套 10 分 21 秒 → 3 分 42 秒。
  # 沒裝 pytest-xdist 就自動補裝一次；裝不起來（離線機）就照舊序列跑——
  # **絕不能因為裝不到而讓部署失敗**，快只是加分，能部署才是底線。
  PAR=""
  if "$VENV/bin/python" -c "import xdist" >/dev/null 2>&1; then
    PAR="-n auto"
  elif "$VENV/bin/pip" install -q pytest-xdist >/dev/null 2>&1        && "$VENV/bin/python" -c "import xdist" >/dev/null 2>&1; then
    PAR="-n auto"
    echo "  （已補裝 pytest-xdist，測試改平行跑）"
  else
    echo "  （沒有 pytest-xdist，測試序列跑，會比較久）"
  fi
  echo "  跑全套 pytest（$_REPO_ROOT/tests）${PAR:+，平行}..."
  if ( cd "$_REPO_ROOT" && "$VENV/bin/python" -m pytest -q $PAR tests ); then
    echo "  ✓ 全套測試通過"
  else
    echo "!! 測試沒過——中止部署，不把未通過測試的碼放上正式機。"
    echo "   要先修測試；確定要略過（緊急）：WEBIT_SKIP_TESTS=1 bash $(basename "${BASH_SOURCE[0]}")"
    exit 1
  fi
else
  echo "!! venv 沒有 pytest，跑不了部署前測試關卡。"
  echo "   家裡 221 應該要能跑，補裝： $VENV/bin/python -m pip install pytest"
  echo "   確定要略過（離線／緊急）：WEBIT_SKIP_TESTS=1 bash $(basename "${BASH_SOURCE[0]}")"
  exit 1
fi

echo "===== [2d/7] 數字核對關卡（用真實資料重驗每頁數字；方案 A 的機制保證）====="
# 2026-09-17 使用者問「你怎麼保證每次邏輯都對？以後改會不會再犯？」——答案就是這一關：
# 每次部署都拿**這台的真實 asset.db** 跑 verify_numbers.py（獨立重算＋加總=下鑽＋子集≤母體
# ＋去重口徑一致等不變式）。任何一次改動弄錯了被覆蓋到的數字，這裡會 ❌ 並**中止部署**，
# 錯的數字上不了線。不靠 AI、不靠人眼看。新增有數字的頁面就要補 verify_numbers 不變式。
if [ "${WEBIT_SKIP_TESTS:-0}" = "1" ]; then
  echo "  ⚠️ WEBIT_SKIP_TESTS=1 → 略過數字核對關卡"
elif [ ! -f "$DATA/asset.db" ]; then
  echo "  尚無 $DATA/asset.db（全新安裝）→ 略過：沒有資料可核對，等有資料後每次部署會驗"
elif [ ! -f "$APP/backend/verify_numbers.py" ]; then
  echo "  找不到 verify_numbers.py → 略過（精簡佈署）"
else
  echo "  用 $DATA/asset.db 跑數字核對..."
  if ASSET_DB_PATH="$DATA/asset.db" "$VENV/bin/python" "$APP/backend/verify_numbers.py"; then
    echo "  ✓ 數字核對全過"
  else
    echo "!! 數字核對有對不上的（上面標 ❌）——中止部署，不把會顯示錯數字的碼放上正式機。"
    echo "   要先修到全過；確定要略過（緊急）：WEBIT_SKIP_TESTS=1 bash $(basename "${BASH_SOURCE[0]}")"
    exit 1
  fi
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

# ---------------------------------------------------------------
# 原則（比這一段的實作更重要）：
#   **安裝腳本的核心任務是把系統裝起來。**
#   周邊的環境調整（防火牆、SELinux、時區…）失敗時一律「警告並繼續」，
#   不要因為裝不了周邊就讓核心任務失敗 —— 那會逼人為了過關去關掉安全機制，更糟。
#   會讓系統「裝不起來或裝壞」的才中止：校驗不符、解壓失敗、service 起不來。
#
# 所以這一段全程用 `|| true` 包著，而且不吃 set -e：
#   · firewalld 沒跑、沒有 firewall-cmd、開埠被拒 → 警告 + 可直接貼的補救指令，繼續裝
#   · 開成功 → **回查 --list-ports 的實際值再印**，不印「已開」這種宣告
#
# 分工（寫清楚，不然收集不到時人會查錯邊）：
#   · 主機層（入站 3000／8000）＝這裡處理
#   · 網段層（出站 22／5985／掃描埠）＝由使用者另向網路單位申請，不在本腳本範圍
# ---------------------------------------------------------------
# 這兩個變數留給 [7/7] 之後的總結用，讓防火牆狀態有一行明確結論，
# 不要藏在中間一大串輸出裡（2026-10-06 使用者要求）。
FW_STATUS="未開"
FW_HINT="之後若需要開埠，執行：
    sudo firewall-cmd --permanent --add-port=${WEB_PORT}/tcp
    sudo firewall-cmd --permanent --add-port=${API_PORT}/tcp
    sudo firewall-cmd --reload"

if ! command -v firewall-cmd >/dev/null 2>&1; then
  FW_STATUS="未開（這台沒有 firewall-cmd）"
  echo "[警告] 未能自動開埠（找不到 firewall-cmd）。安裝繼續，服務照樣會起來。"
  echo "       若這台用的是別的防火牆（nftables／iptables／外部設備），請自行放行"
  echo "       入站 ${WEB_PORT}/tcp 與 ${API_PORT}/tcp。"
elif ! systemctl is-active --quiet firewalld; then
  FW_STATUS="未開（firewalld 未啟用）"
  echo "[警告] 未能自動開埠（firewalld 未啟用）。安裝繼續，服務照樣會起來。"
  echo "       firewalld 沒在跑，通常代表這台不靠它控管入站 —— 但**不要以為埠已經開好了**。"
  echo "       $FW_HINT"
else
  # 指令的離開碼刻意不看 —— 下面一律以回查結果為準。
  firewall-cmd --permanent --add-port=${API_PORT}/tcp >/dev/null 2>&1 || true
  firewall-cmd --permanent --add-port=${WEB_PORT}/tcp >/dev/null 2>&1 || true
  firewall-cmd --reload >/dev/null 2>&1 || true
  # ★ 不信「指令沒報錯」就等於開好了 —— 回查實際生效的清單。
  #   原本的寫法是下完指令就印「已開」，firewall-cmd 失敗也照印，等於騙人。
  _fw_ports="$(firewall-cmd --list-ports 2>/dev/null || true)"
  _miss=""
  case " $_fw_ports " in *" ${API_PORT}/tcp "*) : ;; *) _miss="$_miss ${API_PORT}/tcp" ;; esac
  case " $_fw_ports " in *" ${WEB_PORT}/tcp "*) : ;; *) _miss="$_miss ${WEB_PORT}/tcp" ;; esac
  if [ -z "$_miss" ]; then
    FW_STATUS="已開 ${WEB_PORT}/tcp ${API_PORT}/tcp"
    echo "firewalld: active"
    echo "已開放埠: $_fw_ports   （firewall-cmd --list-ports 回查的實際值）"
  else
    FW_STATUS="未開（回查不到:$_miss）"
    echo "[警告] 未能確認開埠成功。安裝繼續，服務照樣會起來。"
    echo "       回查 firewall-cmd --list-ports 得到： ${_fw_ports:-（空）}"
    echo "       缺少：$_miss"
    echo "       $FW_HINT"
    echo "       若這台的防火牆由集中政策（zone／rich rule／外部設備）控管，請走那個管道申請。"
  fi
fi
export FW_STATUS

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

# [7.7] 每一頁真的打得開嗎（2026-09-20 教訓）：
# 那天 /reports/scan-gaps 的樣板把第二個 <tr> 寫在 v-for 外面 —— pytest 1748 全綠、
# nuxt build 也綠、API 也對，使用者打開卻是整頁空白。Vue 樣板是執行期才解析的，
# 只有真的去打那一頁才知道。tests/smoke_pages.py 本來就為此而生，但沒人叫它。
echo "===== [7.7/7] 逐頁冒煙（SSR 打得開嗎）====="
_tok=""
[ -f /opt/webit3/.verify_token ] && _tok="$(cat /opt/webit3/.verify_token)"
# 這支在 repo 根目錄的 tests/ 底下（APP=<repo>/APP/asset-module）
_SMOKE="$(cd "$APP/../.." 2>/dev/null && pwd)/tests/smoke_pages.py"
if [ -n "$_tok" ] && [ -f "$_SMOKE" ]; then
  if "$VENV/bin/python" "$_SMOKE" --base "$WEB_ORIGIN" --token "$_tok"; then
    echo "  ✓ 每一頁都打得開"
  else
    echo "!! 有頁面打不開（見上面清單）——這種錯測試抓不到，只有打頁面才知道"
    exit 1
  fi
else
  echo "  （跳過：沒有 verify token 或找不到 ${_SMOKE}）"
fi

echo
echo "部署完成。前端： ${WEB_ORIGIN}   後端： ${API_BASE}"
# 防火牆狀態給一行明確結論（成功是回查到的實際值，失敗要看得見）。
echo "防火牆: ${FW_STATUS:-未知}$( [ "${FW_STATUS:-}" = "已開 ${WEB_PORT}/tcp ${API_PORT}/tcp" ] || echo " — 見上方 [警告] 的補救指令" )"
echo "　　　　（入站由本腳本處理；出站 22／${_WINRM_PORT:-5985}／掃描埠屬網段層級，須另向網路單位申請）"

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
