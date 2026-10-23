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
  # 原本寫「此包在出包前已跑過全套」—— 那是推課不是事実：make_install_pack.py 並不跑 pytest。
  # 測試跑過沒跑過是出包那台的事，這裡沒證據，所以不替它背書。
  echo "  離線包情境（偵測到 wheels/）→ 略過：離線機沒測試環境"
  echo "    ⚠ 這一關這次沒守到。全套測試要在出包那台自己跑過（pytest tests），"
  echo "      本脚本無法從這裡確認它跑過了。"
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
  # 同一份程式只跑一次全套（2026-10-19，使用者：「小更新都要30分」）：
  # PC 跑過全套會留一張「通過證明」（.project/pass_stamp.py run --push），檔名＝內容鍵
  # （HEAD 的 APP/ tests/ .project/ 三個 tree hash 算出來的，改一個字就對不上）。
  # 這台核對內容鍵＋工作區乾淨＋failed=0＋同 Python 主版本，全部成立才沿用；
  # **PC 沒實際跑過的（Windows 上 skip 的 Linux 專屬測試等）在這台補跑**，全過才放行。
  # 對不上任何一項 → 照舊跑全套。紅線不變：沒過全套的碼進不了正式機，只是不重跑第二次。
  # 強制在這台重跑全套：WEBIT_FORCE_TESTS=1（或 REQUIRE_TESTS=1）。
  _STAMP_PY="$_REPO_ROOT/.project/pass_stamp.py"
  _STAMP_DIR="$DATA/test_pass"
  _REMAIN="$(mktemp)"
  _stamp_ok=1
  if [ -f "$_STAMP_PY" ] && [ -d "$_STAMP_DIR" ]; then
    ( cd "$_REPO_ROOT" && "$VENV/bin/python" "$_STAMP_PY" check --repo "$_REPO_ROOT"         --dir "$_STAMP_DIR" --remainder "$_REMAIN" ) && _stamp_ok=0 || _stamp_ok=$?
  else
    echo "  沒有全套通過證明（$_STAMP_DIR 不存在）→ 這台跑全套"
  fi
  if [ "$_stamp_ok" = "0" ]; then
    if [ -s "$_REMAIN" ]; then
      echo "  補跑 PC 沒跑到的 $(wc -l < "$_REMAIN") 條${PAR:+（平行）}..."
      if ( cd "$_REPO_ROOT" && "$VENV/bin/python" -m pytest -q -p no:cacheprovider $PAR "@$_REMAIN" ); then
        echo "  ✓ 全套測試通過（PC 全套＋這台補跑）"
      else
        rm -f "$_REMAIN"
        echo "!! 這台補跑的測試沒過——中止部署，不把未通過測試的碼放上正式機。"
        echo "   要先修測試；確定要略過（緊急）：WEBIT_SKIP_TESTS=1 bash $(basename "${BASH_SOURCE[0]}")"
        exit 1
      fi
    else
      echo "  ✓ 全套測試通過（PC 全套已涵蓋這台收得到的每一條）"
    fi
  else
    echo "  跑全套 pytest（$_REPO_ROOT/tests）${PAR:+，平行}..."
    if ( cd "$_REPO_ROOT" && "$VENV/bin/python" -m pytest -q $PAR tests ); then
      echo "  ✓ 全套測試通過"
    else
      rm -f "$_REMAIN"
      echo "!! 測試沒過——中止部署，不把未通過測試的碼放上正式機。"
      echo "   要先修測試；確定要略過（緊急）：WEBIT_SKIP_TESTS=1 bash $(basename "${BASH_SOURCE[0]}")"
      exit 1
    fi
  fi
  rm -f "$_REMAIN"
  # 證明檔只留最新 30 張（一張約 300KB；舊的對不上任何現行內容，留著沒用）
  ls -1t "$_STAMP_DIR"/*.json 2>/dev/null | tail -n +31 | xargs -r rm -f || true
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
# 例外：「掃描空殼自動併回」那段模擬只印 ⚠️ 不擋（2026-10-18；它驗的是「假如現在併」，
# 模擬沒過時 [3a2] 本來就拒寫正式庫——一個功能不能卡住不相干的上線，同 [3a] 的 M3 教訓）。
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
# 生命週期一次性遷移（v1.472.0：資產狀態只有 CIA 可寫）跑在上面這步。它有動到資料時，[2d] 驗的是
# 遷移前的資料——這裡用遷移後的資料再核對一次（審查 #6），有 ❌ 中止；還原：python lifecycle.py revert
if [ -f "$APP/backend/lifecycle.py" ] && [ -f "$APP/backend/verify_numbers.py" ]    && ASSET_DB_PATH="$DATA/asset.db" "$VENV/bin/python" "$APP/backend/lifecycle.py" changed-today; then
  echo "  生命週期遷移今天動到資料 → 用遷移後的資料重跑數字核對..."
  set +e
  ASSET_DB_PATH="$DATA/asset.db" "$VENV/bin/python" "$APP/backend/verify_numbers.py" >/tmp/webit3_lcverify.log 2>&1
  _lc_rc=$?
  set -e
  grep '❌' /tmp/webit3_lcverify.log | head -20 || true
  tail -2 /tmp/webit3_lcverify.log || true
  if [ "$_lc_rc" != "0" ]; then
    echo "!! 遷移後數字核對有 ❌——中止部署。還原遷移：ASSET_DB_PATH=$DATA/asset.db $VENV/bin/python $APP/backend/lifecycle.py revert"
    exit 1
  fi
fi

echo "===== [3a/7] 台數口徑判定＋切換後數字核對（Q79；WEBIT_SKIP_TESTS 也不略過）====="
# [2d] 的數字核對跑在判定之前，驗不到「切換之後」的數字（2026-10-17 審查 #1）。
# 這一步：在副本上判一次＋切換前後各跑一次 verify_numbers，過了才切正式庫（machine_identity.decide_and_verify）。
if [ -f "$APP/backend/machine_identity.py" ]; then
  # rc=3＝試切後數字核對沒過：**沒有切換**、正式庫沒動，維持舊規則——只警告、部署照常繼續
  # （不然每次部署都卡在這裡，連不相干的修正都上不了線；審查 M3）。其他非 0＝這一步本身壞了，中止。
  set +e
  echo "  … 台數口徑判定：第一次會在資料庫副本上核對兩輪，可能要數分鐘到十幾分鐘；已判過會直接略過。請勿中斷。"
  ASSET_DB_PATH="$DATA/asset.db" "$VENV/bin/python" -u "$APP/backend/machine_identity.py" decide-verify
  _mk_rc=$?
  set -e
  if [ "$_mk_rc" = "3" ]; then
    echo "  ⚠️ 台數口徑沒有切換（試切後數字核對沒過，維持舊規則）——部署繼續。原因看上面與 /machine-key。"
    echo "     要永久停在舊規則：系統設定 machine_key_mode=legacy"
  elif [ "$_mk_rc" != "0" ]; then
    echo "!! 台數口徑判定這一步本身出錯（rc=$_mk_rc）——中止部署。看上面的錯誤訊息。"
    exit 1
  fi
fi

echo "===== [3a2/7] 掃描空殼自動併回（決策 DYN2；冪等，已併過的不會再出現）====="
# 證據確定是同一台的 DYN- 空殼（主機名＋IP 相同、CIA 非退役、MAC／序號不衝突、候選全屬同一台）併回 CIA 正本。
# 先在記憶體副本上模擬＋核對（帳外筆數差＝併回筆數、在管台數不變、沒有退役被併），過了才寫正式庫；
# 每筆留快照、3-13 併回紀錄可逐筆還原。跑在 [3a] 台數口徑判定之後（同一台的判定要用定案的口徑）。
# rc=3＝副本核對沒過、正式庫沒動 → 只警告、部署照常；其他非 0＝這一步本身壞了，也只警告（不擋上線）。
if [ -f "$DATA/asset.db" ] && [ -f "$APP/backend/dyn_shell.py" ]; then
  set +e
  ASSET_DB_PATH="$DATA/asset.db" "$VENV/bin/python" -u "$APP/backend/dyn_shell.py" auto-merge
  _dyn_rc=$?
  set -e
  if [ "$_dyn_rc" = "3" ]; then
    echo "  ⚠️ 掃描空殼自動併回：副本核對沒過，正式庫沒動（原因如上）——部署繼續，空殼留在 3-13 由人工處理。"
  elif [ "$_dyn_rc" != "0" ]; then
    echo "  ⚠️ 掃描空殼自動併回這一步出錯（rc=$_dyn_rc）——正式庫維持原狀，部署繼續。"
  fi
fi

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
#   1. Nuxt3 的 .output 是自包含的，執行只需要 node，不需要**開發用的**
#      frontend/node_modules。
#      ⚠️ 「自包含」指的是 .output **自己帶著** server/node_modules（vue-bundle-renderer、
#      @vue、devalue…）。那一份是產物的一部分、不是開發雜物，打包時整個都要帶走；
#      少了就是 ERR_MODULE_NOT_FOUND、前端整個起不來（2026-10-12 正式機實際炸過：
#      離線安裝包的複製規則把它一起濾掉了）。
#   2. apiBase 走 runtimeConfig.public，執行時由 NUXT_PUBLIC_API_BASE 覆蓋（見下面的
#      systemd unit），所以「換一台機器、換一個 IP」不需要重新 build。
# 221 就地開發時 node_modules 與 .output 都在，會走 else 正常 build，行為不變。
if [ -d .output ] && [ ! -d node_modules ]; then
  # ★ 要沿用這份 .output 之前先驗它完不完整 —— 不驗就沿用，等於「不知道」當「沒問題」。
  #   2026-10-12 正式機實況：這裡印「且無 node_modules → 沿用（離線模式）」，
  #   安裝全綠通過，然後前端 500（ERR_MODULE_NOT_FOUND: vue-bundle-renderer）。
  #   原本那句訊息還把「無 node_modules」講得像離線模式的正常狀態 —— 它指的是
  #   **開發用的** frontend/node_modules 不在（那是正常的），但同一句話讀起來像是
  #   「.output 裡面也沒有 node_modules 而且沒關係」（那是包壞了）。措辭一併改掉。
  #
  #   .output/server/node_modules 是 Nuxt 產物的一部分，不是可選的：少了就起不來。
  #   所以缺了一律**中止安裝**，不要用一份跑不起來的前端繼續裝下去 —— 繼續裝的下場是
  #   後面每一關都綠、最後印「安裝完成 ✅」，人卻打不開畫面。
  _FE_NM=".output/server/node_modules"
  _fe_bad=""
  if [ ! -d "$_FE_NM" ]; then
    _fe_bad="整個 frontend/$_FE_NM 目錄不存在"
  elif [ -z "$(ls -A "$_FE_NM" 2>/dev/null)" ]; then
    _fe_bad="frontend/$_FE_NM 存在但是空的"
  elif [ ! -d "$_FE_NM/vue-bundle-renderer" ]; then
    _fe_bad="frontend/$_FE_NM 裡找不到 vue-bundle-renderer（SSR 必要，缺它就是 ERR_MODULE_NOT_FOUND）"
  fi
  if [ -n "$_fe_bad" ]; then
    echo "!! 前端 .output 不完整 —— $_fe_bad"
    echo "!!"
    echo "!! 這不是「離線模式的正常狀態」，是**這份安裝包壞了**。"
    echo "!! .output/server/node_modules/ 是 Nuxt3 build 產物自帶的相依（vue-bundle-renderer、"
    echo "!! @vue、devalue…），執行期一定要在。少了它前端會起來但每個請求都 500："
    echo "!!     ERR_MODULE_NOT_FOUND: Cannot find package 'vue-bundle-renderer'"
    echo "!! 中止安裝，不拿一份跑不起來的前端繼續裝（硬裝下去會全程綠燈、最後打不開畫面）。"
    echo "!!"
    echo "!! 怎麼辦："
    echo "!!   1. 回出包那台重出 app 包： py -3 .project/make_install_pack.py dist --app-only"
    echo "!!      （出包時的 .output 完整性閘門會擋住這種包，所以重出的那份不會再缺）"
    echo "!!   2. 重傳 app 包、重跑 install.sh"
    echo "!!   3. 若是手動部署：在有 node 的機器 npm run build，把整個 .output 原封不動帶過來"
    echo "!!      （複製時不要用「排除 node_modules」這種規則，它會連這一份一起濾掉）"
    exit 1
  fi
  echo "  ✓ 預先 build 的 .output 完整（$_FE_NM 共 $(find "$_FE_NM" -type f 2>/dev/null | wc -l) 個檔，含 vue-bundle-renderer）"
  echo "    → 沿用產物，不需要 npm install/build"
  echo "    （沒有**開發用的** frontend/node_modules 是離線包的正常狀態，不影響執行）"
else
  # npm ci，不是 npm install（2026-10-17）：install 會依「這台的 npm 版本」改寫
  # package-lock.json（221 的 npm 10.8.2 會刪掉新版 npm 寫的 libc 欄位，每次 114 行），
  # 部署完 repo 就髒了，/api/version 一直回 dirty:true，真正的改動被它蓋掉看不出來。
  # ci 嚴格照 lock 裝、從不寫回 lock；lock 跟 package.json 對不上時直接失敗（那是該修的錯，不該默默改）。
  #
  # 前端沒改就不重 build（2026-10-19）：npm ci＋build 每次約 50 秒，小改動多半只動後端。
  # 「沒改」的判準是 git 依內容算的 frontend/ tree hash（含 package-lock.json），
  # 加上 node 版本與 API_BASE，跟上次成功 build 時記下的完全相同，而且 frontend/ 沒有
  # 未提交改動、.output 完整（含 vue-bundle-renderer）。任一不符 → 照舊 npm ci＋build。
  # 強制重 build：WEBIT_FORCE_BUILD=1。
  _FE_STAMP="$DATA/frontend_build.stamp"
  _FE_TREE="$(git -C "$APP/frontend" rev-parse HEAD:./ 2>/dev/null || true)"
  _FE_WANT="tree=${_FE_TREE} node=$(node -v 2>/dev/null || echo none) api=${API_BASE}"
  _FE_DIRTY="$(git -C "$APP/frontend" status --porcelain --untracked-files=all -- . 2>/dev/null || echo '?')"
  if [ "${WEBIT_FORCE_BUILD:-0}" != "1" ] && [ -n "$_FE_TREE" ] && [ -z "$_FE_DIRTY" ] \
     && [ -f .output/server/index.mjs ] && [ -d .output/server/node_modules/vue-bundle-renderer ] \
     && [ -f "$_FE_STAMP" ] && [ "$(cat "$_FE_STAMP" 2>/dev/null)" = "$_FE_WANT" ]; then
    echo "  前端內容跟上次 build 的一樣（frontend tree ${_FE_TREE:0:12}）→ 沿用現有 .output，不重 build"
    echo "  （強制重 build：WEBIT_FORCE_BUILD=1）"
  else
    rm -f "$_FE_STAMP"
    npm ci --no-audit --no-fund
    NUXT_PUBLIC_API_BASE="$API_BASE" npm run build
    # 只有 build 成功（set -e 沒中止）才記；工作區有改動時不記，下次一定重 build
    if [ -n "$_FE_TREE" ] && [ -z "$_FE_DIRTY" ]; then
      echo "$_FE_WANT" > "$_FE_STAMP"
    fi
  fi
fi

echo "===== [4.5/7] stamp 版本建置資訊（/api/version 用，讓畫面看得出換版成功）====="
# git_commit 優先自己從 repo 取——221 現在是 git clone（原本的註解假設「無 git repo」
# 已經不成立）。取不到才退回外部帶進來的 GIT_COMMIT（離線包/公司主機那種情境）。
#
# 2026-08-25 踩到：這一步漏掉的後果是 /api/version 回報一個**過期的 commit**，
# 而畫面上它看起來跟版號一樣確定。當時 221 顯示 `1d8464f`、實際 HEAD 是 `a6d7467`，
# 差 74 個 commit——拿它去比對排查會整個查錯方向。
# 字面值 "n/a" 要跟「沒設」一樣處理：setup.sh 取不到 commit 時會 export GIT_COMMIT=n/a，
# 原本的 `[ -z ... ]` 判不到它（非空字串），於是就算這台有 git 也不會去問 repo。
if { [ -z "${GIT_COMMIT:-}" ] || [ "${GIT_COMMIT:-}" = "n/a" ]; } \
   && git -C "$APP" rev-parse --short HEAD >/dev/null 2>&1; then
  GIT_COMMIT="$(git -C "$APP" rev-parse --short HEAD)"
fi
printf '{"git_commit":"%s","built_at":"%s"}\n' "${GIT_COMMIT:-n/a}" "$(date '+%Y-%m-%d %H:%M')" \
  > "$APP/backend/build_info.json"
if [ -z "${GIT_COMMIT:-}" ] || [ "${GIT_COMMIT:-}" = "n/a" ]; then
  # 寫了一個 n/a 進去就要講出來：下游 [7.6] 會因此「驗不了」，而不是「通過」。
  echo "  ⚠ stamp: n/a —— 這次標不出是哪一份 commit"
  echo "     離線包的 commit 應該由出包時寫進 app 包根目錄的 PACK_COMMIT，setup.sh 讀它當 GIT_COMMIT。"
  echo "     兩邊都沒給值才會變 n/a → [7.6/7] 這一關這次驗不了（不是通過）。"
else
  echo "  stamp: ${GIT_COMMIT}"
fi

echo "===== [5/7] systemd services + timers ====="
# 台數口徑的環境變數回退（WEBIT3_MACHINE_KEY=legacy）是人手動加進 unit 的——重寫 unit 時帶過去，
# 不然部署一次就被洗掉（審查 #9）。主要的回退方式是 app_settings（machine_identity.py rollback）。
MK_ENV_LINE="$(grep -h '^Environment=WEBIT3_MACHINE_KEY=' /etc/systemd/system/webit3-api.service 2>/dev/null | head -1 || true)"  # 沒有這行時 grep 回 1：set -euo pipefail 下不加 || true 會讓整支腳本在這裡靜默結束（2026-10-17 221 實際踩到）
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
${MK_ENV_LINE}
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
# ★ 三態，不是兩態（2026-10-12 修）。
#   原本的寫法是 `[ "$_head" != "n/a" ] && { 比對 }`：repo HEAD 取不到就整段跳過，
#   直接印「✓ 一致」。正式機 17:35 那次就是三個值**全部是 n/a**，它照樣印「✓ 一致」，
#   於是「版號會騙人、這裡驗真的」這一關自己變成整個流程裡最會騙人的一關 ——
#   任何裝壞的情況都能一路綠燈走到「安裝完成 ✅」。
#
#   這跟專案既有的戒律是同一條：`Error` 不准併進 `Compliant`、`changed` 的 NULL
#   不准塌成 0、空白要分得出是哪一種空白。**「不知道」不等於「沒問題」。**
#
#   三態：
#     · 三個值都取到且相同          → ✓ 一致
#     · 取到的值之間有不同          → ✗ 不一致，中止（這是真的壞了）
#     · 有值取不到（n/a）           → ⚠ 驗不了，明講「無法確認跑的是不是新碼」，不中止
#   「驗不了」刻意不中止：離線安裝沒有 git，repo HEAD 本來就取不到，不該因此擋掉安裝。
#   但它**絕對不能印「一致」** —— 驗不了就是驗不了，要讓人知道這一關這次沒有保護到他。
_known=""; _n_known=0; _na_which=""
for _pair in "HEAD:$_head" "stamp:$_stamp" "API:$_api"; do
  _k="${_pair%%:*}"; _v="${_pair#*:}"
  case "$_v" in
    ""|n/a|N/A|null) _na_which="$_na_which $_k" ;;
    *) _n_known=$((_n_known + 1)); _known="$_known $_v" ;;
  esac
done
# 取到的那幾個先彼此比對：就算只有兩個有值，兩個不同一樣是確定壞了，不用等第三個。
_mismatch=0; _first=""
for _v in $_known; do
  [ -z "$_first" ] && _first="$_v"
  [ "$_v" = "$_first" ] || _mismatch=1
done
if [ "$_mismatch" = 1 ]; then
  VERIFY_STATUS="✗ 不一致（$_known）"
  echo "  ✗ 不一致——畫面上的版本資訊會騙人，請查上面哪一步沒生效"
  echo "     HEAD ≠ stamp → [4.5] 沒跑到；stamp ≠ API → 服務沒真的重啟，還是舊行程在跑"
  exit 1
elif [ "$_n_known" -lt 3 ]; then
  VERIFY_STATUS="⚠ 驗不了（取不到:$_na_which）"
  echo "  ⚠ 驗不了——**無法確認跑的是不是新碼**（取不到的值：$_na_which）"
  echo "     這一關不是通過，是沒驗到。取到的那幾個彼此一致，但那證明不了什麼。"
  # 每一個取不到的值只印它自己的後果與查法，不要把三段全印——印不相關的那幾段
  # 本身就是一種誤導（讀的人会以為 HEAD 也沒取到）。
  case " $_na_which " in *" HEAD "*)
    echo "     · repo HEAD 取不到：離線安裝沒有 git repo，**這是預期的**。"
    echo "       但那代表「這一關在離線情境下驗不了」，不是「驗過了而且通過」。" ;;
  esac
  case " $_na_which " in *" stamp "*)
    echo "     · stamp 取不到：**這是問題**。stamp 由 [4.5] 自己寫，離線安裝也該有值。"
    echo "       查：出包時有沒把 commit 寫進 app 包根目錄的 PACK_COMMIT，"
    echo "           以及 setup.sh 步驟 6 有沒把 GIT_COMMIT 帶進 deploy.sh。" ;;
  esac
  case " $_na_which " in *" API "*)
    echo "     · API 回報取不到：**這是問題**。API 讀的是同一份 build_info.json。"
    echo "       查： curl -s ${API_BASE}/api/version   （不通？沒 git_commit 欄？讀不到檔？）" ;;
  esac
  echo "     安裝不因此中止（這一關只是驗收，不是安裝步驟），但換版之後請自己確認跑的是新碼。"
else
  VERIFY_STATUS="✓ 一致（$_head）"
  echo "  ✓ 一致"
fi

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
  # --api：拿來現查一筆資產序號，巡資產詳細頁的分頁（/assets/{serial}?tab=schedule）
  if "$VENV/bin/python" "$_SMOKE" --base "$WEB_ORIGIN" --api "$API_BASE" --token "$_tok"; then
    SMOKE_STATUS="✓ 每一頁都打得開"
    echo "  ✓ 每一頁都打得開"
  else
    echo "!! 有頁面打不開（見上面清單）——這種錯測試抓不到，只有打頁面才知道"
    exit 1
  fi
else
  # 跳過也要有狀態：原本只印一行「（跳過…）」就過去，而整份安裝最後照樣印
  # 「安裝完成 ✅」—— 讀的人分不出「每頁都驗過」跟「一頁都沒驗」。同一條戒律。
  _sk=""
  [ -n "$_tok" ] || _sk="沒有 verify token"
  [ -f "$_SMOKE" ] || _sk="${_sk:+$_sk，}找不到 $_SMOKE"
  SMOKE_STATUS="⚠ 沒驗到（${_sk}）"
  echo "  $SMOKE_STATUS —— 頁面打不打得開這次沒有驗，不代表都正常"
fi

# [7.8] 端到端驗收（決策 ACCEPT-E2E1，2026-10-18 使用者方向）：
# 使用者在公司機重新納管後，資產頁 OS 重建「尚未收集」。之前的「驗證」是直接呼叫內部函式，
# 沒走瀏覽器那條路——驗了等於沒驗。這一關只打瀏覽器會打的 HTTP 端點：重新納管收集器自己
# （OnboardModal「本機執行」＋「驗證」，不需要密碼）→ 等納管後六樣 → 用資產頁分頁讀的端點
# 確認 OS 重建／組態檢核是**這次之後**寫進去的。六樣任一樣沒收到（且不是正當略過）→ 中止部署。
#   · WEBIT_E2E_ACCEPT=manual：patch.sh／setup.sh 用（公司機不保證收集器自己已納管）→ 印手動三步驟
#   · WEBIT_SKIP_E2E=1：緊急略過（大聲警告；這次部署沒有證明納管後資料進得來）
echo "===== [7.8/7] 端到端驗收（重新納管收集器自己 → 六樣 → 資產頁分頁；ACCEPT-E2E1）====="
_E2E_PY="$APP/deploy_e2e_accept.py"
_e2e_manual() {
  echo "  請在畫面上手動驗收（三步，約 3 分鐘）："
  echo "   1. 2-1 已納管主機 → 挑一台 Linux 按「↻ 重新納管」→ 完成（帳密或本機執行＋驗證都可以）"
  echo "   2. 打開那台的資產頁，等上方那一列六樣跑完（收集中… → ✓／✕／略過）"
  echo "   3. 點「OS 重建」與「組態檢核」分頁：最後收集時間要是剛剛；空的話分頁裡會寫原因——把那句貼回來"
}
if [ "${WEBIT_SKIP_E2E:-}" = "1" ]; then
  E2E_STATUS="⚠ 已略過（WEBIT_SKIP_E2E=1）"
  echo "  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  echo "  !! WEBIT_SKIP_E2E=1：端到端驗收**略過**——這次部署沒有證明「重新納管後"
  echo "  !! OS 重建／組態檢核進得來」。請自己照下面三步驗一次。"
  echo "  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  _e2e_manual
elif [ "${WEBIT_E2E_ACCEPT:-auto}" = "manual" ]; then
  E2E_STATUS="⚠ 未自動驗（patch／安裝：請照三步手動驗收）"
  echo "  這台不保證有自己納管自己，端到端改手動驗收。"
  _e2e_manual
elif [ ! -f "$_E2E_PY" ]; then
  E2E_STATUS="✗ 找不到 $_E2E_PY"
  echo "  ✗ 找不到 $_E2E_PY——這一關不能「沒驗到就當通過」，中止"
  exit 1
fi
# 沒改到納管／收集那條路就不重新納管（2026-10-19，使用者：「小更新都要30分」）。
# 比對對象是「上一次端到端驗收**通過**的 commit」（$DATA/e2e_last_pass），不是上一次部署的——
# 上次部署若卡在這一關，下一次一定重驗，不會被一個不相干的小改動帶過去。
# 判定為相關的：納管／收集／帳號遷移／組態檢核／OS 重建／SSH／WinRM 的程式與腳本、
# 資料庫結構（db.py／schema.sql）、api.py、相依、這支腳本與驗收腳本本身、資產頁前端。
# 沒有紀錄、紀錄的 commit 找不到、工作區有改動 → 一律跑。強制跑：WEBIT_FORCE_E2E=1。
_E2E_MARK="$DATA/e2e_last_pass"
_E2E_RE='(onboard|collect|agent|bootstrap|config_audit|host_spec|rebuild|ssh|winrm|manage_state|machine_scope)[^/]*$|/agent_scripts/|backend/(db\.py|schema\.sql|api\.py|requirements\.txt)$|asset-module/(deploy\.sh|deploy_e2e_accept\.py)$|frontend/pages/assets/'
_E2E_NEED=1; _E2E_WHY=""
if [ "${WEBIT_SKIP_E2E:-}" != "1" ] && [ "${WEBIT_E2E_ACCEPT:-auto}" != "manual" ] \
   && [ "${WEBIT_FORCE_E2E:-0}" != "1" ] && [ -f "$_E2E_MARK" ]; then
  _e2e_prev="$(head -1 "$_E2E_MARK" 2>/dev/null | tr -d '[:space:]')"
  if [ -n "$_e2e_prev" ] && git -C "$APP" cat-file -e "${_e2e_prev}^{commit}" 2>/dev/null \
     && [ -z "$(git -C "$APP" status --porcelain --untracked-files=no -- . 2>/dev/null || echo '?')" ]; then
    _e2e_hits="$(git -C "$APP" diff --name-only "$_e2e_prev" HEAD -- . 2>/dev/null | grep -E "$_E2E_RE" || true)"
    if [ -z "$_e2e_hits" ]; then
      _E2E_NEED=0
      _E2E_WHY="上次端到端通過的 ${_e2e_prev:0:8} 之後沒改到納管／收集相關程式"
    else
      _E2E_WHY="改到納管／收集相關：$(echo "$_e2e_hits" | head -3 | tr '\n' ' ')"
    fi
  fi
fi
if [ "${WEBIT_SKIP_E2E:-}" = "1" ] || [ "${WEBIT_E2E_ACCEPT:-auto}" = "manual" ]; then
  :
elif [ "$_E2E_NEED" = "0" ]; then
  # 輕量冒煙：納管入口端點打得通（唯讀；不重新納管、不動任何主機）
  _e2e_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
               ${_tok:+-H "Cookie: session_token=${_tok}"} \
               "${API_BASE}/api/onboard/script?platform=linux" 2>/dev/null || echo 000)"
  if [ "$_e2e_code" = "200" ]; then
    E2E_STATUS="✓ 輕量（${_E2E_WHY}；納管入口 /api/onboard/script 200）"
    echo "  $E2E_STATUS"
    echo "  （要重新納管完整驗一次：WEBIT_FORCE_E2E=1）"
  else
    echo "!! 輕量冒煙：納管入口 /api/onboard/script 回 ${_e2e_code}（不是 200）——中止，不算部署成功"
    echo "   完整驗一次：WEBIT_FORCE_E2E=1 bash deploy.sh"
    exit 1
  fi
else
  [ -n "$_E2E_WHY" ] && echo "  要重新納管完整驗：$_E2E_WHY"
  _E2E_DIR="$DATA/e2e_evidence/$(date +%Y%m%d_%H%M%S)"
  if "$VENV/bin/python" "$_E2E_PY" --api "$API_BASE" --web "$WEB_ORIGIN" --ip "$API_HOST" \
       --mode script --token "${_tok:-}" --evidence "$_E2E_DIR" --wait "${WEBIT_E2E_WAIT:-600}"; then
    E2E_STATUS="✓ 重新納管後六樣都進來、資產頁分頁讀得到（證據 $_E2E_DIR）"
    echo "  $E2E_STATUS"
    git -C "$APP" rev-parse HEAD > "$_E2E_MARK" 2>/dev/null || rm -f "$_E2E_MARK"
  else
    echo "!! 端到端驗收不通過（證據 $_E2E_DIR）——重新納管後資產頁的資料進不來，不算部署成功"
    echo "   緊急要先上線：WEBIT_SKIP_E2E=1 bash deploy.sh（會大聲警告，事後一定要補驗）"
    exit 1
  fi
fi
chown -R "${SVC_USER}:${SVC_USER}" "$DATA/e2e_evidence" 2>/dev/null || true

echo
echo "部署完成。前端： ${WEB_ORIGIN}   後端： ${API_BASE}"
# 驗收狀態攤在結尾：這兩關可能是「通過」、也可能是「沒驗到」，兩者差很多，
# 不能都藏在中間一大串輸出裡讓人以為整份綠燈就是都驗過了。
echo "換版驗證: ${VERIFY_STATUS:-⚠ 未執行}"
echo "逐頁冒煙: ${SMOKE_STATUS:-⚠ 未執行}"
echo "端到端　: ${E2E_STATUS:-⚠ 未執行}"
# 留一份給 setup.sh 的結尾橫幅：deploy.sh 是子行程，變數帶不回去，但那張
# 「安裝完成 ✅」的橫幅不能在「其實沒驗到」的時候看起來像全部驗過了。
{
  echo "VERIFY_STATUS=${VERIFY_STATUS:-⚠ 未執行}"
  echo "SMOKE_STATUS=${SMOKE_STATUS:-⚠ 未執行}"
  echo "E2E_STATUS=${E2E_STATUS:-⚠ 未執行}"
  echo "FW_STATUS=${FW_STATUS:-未知}"
} > "$DATA/.deploy_verify" 2>/dev/null || true
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
