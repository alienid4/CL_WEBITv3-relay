#!/usr/bin/env bash
# 戰情室資產盤點 — 正式區「一鍵」離線安裝
# ===============================================================
# 使用者只要打這一行，其餘全部由這支腳本自己處理：
#
#     sudo bash /tmp/install.sh
#
# 它會自己做完：搬到安全工作區 → sha256 校驗 → 合併分割檔 → 解 runtime 包
# → 解 app 包 → 跑 setup.sh → 印驗收結果。
# **不需要人先 mkdir、chmod、mv 或手動 sha256sum。**
#
# 前提：把下載到的檔案跟這支腳本放在同一個目錄（放 /tmp 沒問題，它會自己搬）：
#     install.sh
#     webit3-runtime-linux-x64-py311-node20.tar.gz.part00 .part01 .part02
#     webit3-runtime-linux-x64-py311-node20.tar.gz.splits.sha256
#     webit3-runtime-linux-x64-py311-node20.tar.gz.sha256
#     webit3-app-v<版本>.tar.gz
#     webit3-app-v<版本>.tar.gz.sha256
#
# runtime 包（可攜 Python＋Node＋離線 wheels，約 93 MB）**只有第一次要傳**。
# 之後升版只傳 app 包（約 12 MB）—— 這支腳本偵測到 runtime 已經裝好就整段跳過。
#
# 任何一步失敗都會印「是什麼失敗 + 該怎麼辦」，不是只回非 0。
# ---------------------------------------------------------------
set -uo pipefail

# 程式安裝路徑沿用 setup.sh 的既有預設，這裡不改也不覆蓋：
#   /opt/webit3/app  /opt/webit3/data  /opt/webit3/venv  /opt/webit3/runtime
# 這支只決定「安裝工作區」在哪。
WORK="${WEBIT_INSTALL_WORK:-/opt/webit3/install}"
RUNTIME="${WEBIT_RUNTIME:-/opt/webit3/runtime}"
RT_SRC="$RUNTIME/.src"
RT_PACK_GLOB="webit3-runtime-*.tar.gz"
APP_PACK_GLOB="webit3-app-*.tar.gz"

STEP=0
step() { STEP=$((STEP+1)); echo; echo "===== [$STEP] $1 ====="; }

# 失敗一律走這裡：講清楚哪一步、什麼原因、下一步做什麼。
die() {
  echo
  echo "════════════════════════════════════════════════════════════"
  echo "!! 安裝停止 —— 卡在第 $STEP 步"
  echo "!!"
  while [ "$#" -gt 0 ]; do echo "!! $1"; shift; done
  echo "════════════════════════════════════════════════════════════"
  # 已經動到舊版（INSTALL_STARTED=1）而且是重裝 → 自動還原，不把正式機留在半套狀態。
  if [ "${INSTALL_STARTED:-0}" = "1" ] && [ "${REINSTALL:-0}" = "1" ] && [ "${ROLLED_BACK:-0}" = "0" ]; then
    rollback_install
  elif [ "${SVC_STOPPED:-0}" = "1" ] && [ "${INSTALL_STARTED:-0}" = "0" ]; then
    # 備份階段就失敗：什麼都沒改，把為了備份而停掉的服務拉回來
    systemctl start webit3-api webit3-web 2>/dev/null || true
    echo "  （已把先前停掉的服務重新啟動，舊版照常運作）"
  fi
  exit 1
}

# ---- 重裝失敗的自動還原 ----
# 還原內容：app（backend／frontend／deploy.sh）＋ asset.db（備份當下一致性副本）。
# 為什麼連 DB 一起還原：新版程式啟動時可能已經做過 schema 遷移，只還原舊程式碼會讓
# 舊程式面對新結構的資料庫。重裝期間服務是停著的，備份之後沒有新資料寫入，所以整份還原
# 不會丟東西。其餘 data（收集金鑰、設定檔、log）裝的時候本來就不動，不需要還原。
APP_DIR="${WEBIT_APP:-/opt/webit3/app}"
DATA_DIR="${WEBIT_DATA:-/opt/webit3/data}"
VENV_DIR="${WEBIT_VENV:-/opt/webit3/venv}"
BK_DIR=""            # 備份目錄（重裝時才會有）
OLD_VER=""           # 舊版號（重裝時才會有）
REINSTALL=0
INSTALL_STARTED=0
ROLLED_BACK=0
BK_OK=0              # 備份全部成功才為 1；還原只在 1 時才動舊檔
SVC_STOPPED=0

rollback_install() {
  ROLLED_BACK=1
  echo
  echo "════════════════════════════════════════════════════════════"
  echo "↩ 自動還原到安裝前的版本（${OLD_VER:-未知}）"
  if [ "$BK_OK" != "1" ] || [ -z "$BK_DIR" ] || [ ! -f "$BK_DIR/app.tar.gz" ]; then
    echo "!! 備份不完整或找不到（$BK_DIR/app.tar.gz），不動現有檔案、無法自動還原。請交給 AI／維運處理，不要重複執行。"
    echo "════════════════════════════════════════════════════════════"
    return 1
  fi
  # 先驗備份、先解到暫存目錄，確定解得開才換掉現有的（備份壞了不能先把現有的刪掉）
  _tmp="$APP_DIR/.rollback_tmp"
  rm -rf "$_tmp"; mkdir -p "$_tmp"
  if ! tar xzf "$BK_DIR/app.tar.gz" -C "$_tmp" || [ ! -f "$_tmp/backend/version.json" ]; then
    echo "!! 備份檔解不開或內容不對（缺 backend/version.json），現有檔案原封不動。備份在 $BK_DIR"
    rm -rf "$_tmp"
    echo "════════════════════════════════════════════════════════════"
    return 1
  fi
  systemctl stop webit3-web webit3-api 2>/dev/null || true
  rm -rf "$APP_DIR/backend" "$APP_DIR/frontend" "$APP_DIR/deploy.sh"
  mv "$_tmp/backend" "$APP_DIR/backend" && mv "$_tmp/frontend" "$APP_DIR/frontend"     && { [ ! -f "$_tmp/deploy.sh" ] || mv "$_tmp/deploy.sh" "$APP_DIR/deploy.sh"; }
  rm -rf "$_tmp"
  if [ -f "$BK_DIR/asset.db" ]; then
    # 新版若已遷移過 DB：先把現況另存（不丟證據），清掉 WAL／SHM 再還原，避免新版的 WAL 重播到舊 DB 上
    [ -f "$DATA_DIR/asset.db" ] && cp -f "$DATA_DIR/asset.db" "$BK_DIR/asset.db.pre_rollback" 2>/dev/null
    rm -f "$DATA_DIR/asset.db-wal" "$DATA_DIR/asset.db-shm"
    cp -f "$BK_DIR/asset.db" "$DATA_DIR/asset.db" && echo "  ✓ asset.db 已還原（現況另存為 asset.db.pre_rollback）"
  fi
  # install.conf 與 systemd unit 也還原（新包可能改過 HTTPS 模式／埠）
  [ -f "$BK_DIR/install.conf" ] && cp -f "$BK_DIR/install.conf" "$DATA_DIR/install.conf"
  for _u in "$BK_DIR"/webit3-*.service; do
    [ -f "$_u" ] && cp -f "$_u" "/etc/systemd/system/$(basename "$_u")"
  done
  systemctl daemon-reload 2>/dev/null || true
  _svc="$(sed -n 's/^SVC_USER=//p' "$DATA_DIR/install.conf" 2>/dev/null | head -1)"
  [ -n "$_svc" ] && chown -R "$_svc:$_svc" "$APP_DIR" "$DATA_DIR/asset.db" 2>/dev/null
  systemctl restart webit3-api webit3-web 2>/dev/null || true
  sleep 3
  _ra="$(systemctl is-active webit3-api 2>/dev/null)"; _rw="$(systemctl is-active webit3-web 2>/dev/null)"
  echo "  服務狀態： api=$_ra web=$_rw"
  if [ "$_ra" != "active" ] || [ "$_rw" != "active" ]; then
    echo "!! 還原後服務沒有全部起來（api=$_ra web=$_rw）。備份在 $BK_DIR，請交給 AI／維運處理。"
    echo "════════════════════════════════════════════════════════════"
    return 1
  fi
  echo "  還原後版本： $(sed -n 's/.*"version": *"\([^"]*\)".*//p' "$APP_DIR/backend/version.json" 2>/dev/null | head -1)"
  echo "  ⚠ 沒有還原的：nginx 設定、防火牆、venv 套件（舊程式相容新套件，通常無妨）"
  echo "  備份保留在： $BK_DIR"
  echo "════════════════════════════════════════════════════════════"
  return 0
}

echo "戰情室資產盤點 · 正式區一鍵離線安裝"
echo "開始時間： $(date '+%F %T')"

# ---------------------------------------------------------------
step "確認權限"
# ---------------------------------------------------------------
if [ "$(id -u)" != "0" ]; then
  die "這支要用 root 跑（要建 /opt 底下的目錄、建 systemd service、開防火牆埠）。" \
      "" \
      "請改成：  sudo bash $0"
fi
echo "  ✓ 以 root 執行"

HERE="$(cd "$(dirname "$0")" && pwd)"
echo "  目前位置： $HERE"

# ---------------------------------------------------------------
step "搬到安全工作區（$WORK）"
# ---------------------------------------------------------------
# 為什麼一定要搬（稽核會問，答案要一句話講得完）：
# /tmp 這類 other-writable 目錄**任何本機帳號都能寫**。root 執行放在那裡的腳本，
# 等於在「校驗完」與「真的執行」之間留一個換檔空窗 —— 本機任何使用者都能把腳本或
# 解開後的程式換掉，讓 root 幫他執行任意程式碼。那是本機提權，不是外觀問題。
# 這個專案踩過同一型的真實漏洞（舊的納管腳本去撿世界可寫目錄裡的 install.sh 交給
# root 安裝），所以這裡不是理論風險。
#
# 下載落地在 /tmp 沒關係 —— 但不在那裡執行。搬到 root 專屬的 0700 目錄再繼續。
# 不要人工 mkdir / chmod / mv：由腳本自己做，才叫一鍵。
_other_writable() {
  # ls -ld 第一欄是 10 碼（型別 + 9 個權限碼），其他人的 w 在第 9 碼。
  # 純 POSIX，不依賴 GNU stat 的 %a。
  _perm="$(ls -ld "$1" 2>/dev/null | awk '{print $1}')"
  [ "$(printf '%s' "$_perm" | cut -c9)" = "w" ]
}

path_is_unsafe() {
  _p="$1"
  while : ; do
    _other_writable "$_p" && { echo "$_p"; return 0; }
    [ "$_p" = "/" ] && break
    _p="$(dirname "$_p")"
  done
  case "$1" in /tmp|/tmp/*|/var/tmp|/var/tmp/*) echo "$1"; return 0 ;; esac
  return 1
}

if [ "$HERE" = "$WORK" ]; then
  echo "  ✓ 已經在工作區裡，不用搬"
else
  _unsafe="$(path_is_unsafe "$HERE" || true)"
  if [ -n "$_unsafe" ]; then
    echo "  目前路徑不安全（世界可寫）： $_unsafe"
    echo "    $(ls -ld "$_unsafe" 2>/dev/null)"
  fi
  echo "  → 複製安裝檔到 $WORK"

  mkdir -p "$WORK" || die "建不出工作區 $WORK。" \
      "可能是 /opt 不可寫或磁碟滿了，先看： df -h /opt"
  chmod 700 "$WORK" || die "設不了 $WORK 的權限（要 0700）。"
  chown root:root "$WORK" 2>/dev/null || true

  # 工作區自己也要查一次：萬一 /opt 被人改成世界可寫，搬過去一樣沒意義。
  _unsafe_work="$(path_is_unsafe "$WORK" || true)"
  [ -n "$_unsafe_work" ] && die \
      "工作區 $WORK 的路徑上有世界可寫的目錄： $_unsafe_work" \
      "  $(ls -ld "$_unsafe_work" 2>/dev/null)" \
      "" \
      "這代表 /opt 底下的權限被動過，請先交資安／系統管理確認再裝。"

  _copied=0
  for f in "$0" "$HERE"/webit3-*.tar.gz "$HERE"/webit3-*.tar.gz.part[0-9][0-9] \
           "$HERE"/webit3-*.sha256 ; do
    [ -f "$f" ] || continue
    _b="$(basename "$f")"
    cp -f "$f" "$WORK/$_b" || die "複製 $_b 到 $WORK 失敗。" \
        "磁碟空間不足是最常見的原因，先看： df -h /opt"
    # 搬一半就往下跑會裝出半套東西，所以每個檔都比對大小再算過關。
    _s1="$(wc -c < "$f")"; _s2="$(wc -c < "$WORK/$_b")"
    [ "$_s1" = "$_s2" ] || die "複製後大小不符： $_b（來源 $_s1 / 目的 $_s2 bytes）" \
        "" \
        "通常是磁碟空間不足（看 df -h /opt），清出空間後重跑這支。"
    _copied=$((_copied+1))
    echo "    ✓ $_b（$_s2 bytes）"
  done
  [ "$_copied" -gt 0 ] || die \
      "在 $HERE 找不到任何安裝檔（webit3-*.tar.gz / .part* / .sha256）。" \
      "" \
      "請確認下載的檔案跟 install.sh 放在同一個目錄，再重跑。"
  echo "  ✓ 已複製 $_copied 個檔案到 $WORK，接下來在那裡繼續"
  echo
  # 在安全目錄重新執行自己。沒有略過用的環境變數 —— 判斷純粹看「現在在哪」，
  # 能被略過的防護等於沒有防護。
  exec bash "$WORK/$(basename "$0")"
fi

cd "$WORK" || die "進不去 $WORK"

# ---------------------------------------------------------------
step "盤點手上有哪些安裝檔"
# ---------------------------------------------------------------
HAVE_RUNTIME_PACK=0
HAVE_RUNTIME_PARTS=0
APP_PACK=""

ls $RT_PACK_GLOB            >/dev/null 2>&1 && HAVE_RUNTIME_PACK=1
ls $RT_PACK_GLOB.part[0-9][0-9] >/dev/null 2>&1 && HAVE_RUNTIME_PARTS=1

# runtime 包只准有一包。
# 2026-10-13：這裡原本也是 `ls ... | head -1`（跟 app 包同一型的洞）。
# app 包可以用版本序挑最新，runtime 不行——app 要的是**相容**的 runtime，
# 不是版號最大的那個。所以多於一包時不猜，停下來讓人指定。
_n_rt="$(ls -1 $RT_PACK_GLOB 2>/dev/null | grep -v '\.part' | grep -c . || true)"
if [ "$_n_rt" -gt 1 ]; then
  die "這個目錄裡有 $_n_rt 包 runtime，我不知道該用哪一包。" \
      "" \
      "看到的包：" \
      "$(ls -1 $RT_PACK_GLOB 2>/dev/null | grep -v '\.part' | sed 's/^/  /')" \
      "" \
      "runtime 要跟 app 相容，不是挑版號大的。請只留要用的那一包，其餘移走後重跑。"
fi
# 挑版本最新的那一包。
#
# 2026-10-13：原本是 `ls -1 ... | head -1`。ls 是字典序，v1.412.0 排在
# v1.416.0 前面 → 兩包都在時永遠裝到最舊那包。公司正式機就是這樣解了
# 1.416.0 卻跑 1.412.0，前端 500。症狀是「裝了新版但沒生效」，最難查，
# 因為每一步看起來都成功。
#
# 改用 sort -V（版本序）取最後一筆。sort -V 不在時不猜，直接停下來把看到
# 的包列給人看——安靜地挑一包正是這次的 bug，修法不該再造一個同型的洞。
APP_PACK=""
APP_PACK_IGNORED=""
_cands="$(ls -1 $APP_PACK_GLOB 2>/dev/null || true)"
_n_cands="$(printf '%s\n' "$_cands" | grep -c . || true)"
if [ "$_n_cands" -gt 1 ]; then
  if printf '1.9.0\n1.10.0\n' | sort -V >/dev/null 2>&1; then
    APP_PACK="$(printf '%s\n' "$_cands" | sort -V | tail -1)"
    APP_PACK_IGNORED="$(printf '%s\n' "$_cands" | grep -vxF "$APP_PACK" | tr '\n' ' ')"
  else
    die "這個目錄裡有 $_n_cands 包 app，而這台的 sort 不支援版本排序（-V），我無法確定哪一包是新的。" \
        "" \
        "看到的包：" \
        "$(printf '%s\n' "$_cands" | sed 's/^/  /')" \
        "" \
        "請只留要裝的那一包，其餘移走後重跑。"
  fi
elif [ "$_n_cands" -eq 1 ]; then
  APP_PACK="$_cands"
fi

RUNTIME_READY=0
if [ -x "$RUNTIME/python311/bin/python3" ] && [ -x "$RUNTIME/node/bin/node" ]; then
  RUNTIME_READY=1
fi

# 手上有 runtime 包＝使用者這次特地帶來的 → 一律重新解開（換新 wheels），不因為
# 「runtime 已裝好」就跳過。只傳 app 包的升版（沒有 runtime 包）才沿用舊的。
# 起因 2026-11-16：正式機 runtime 早已裝好，新 app 需要的 pypsrp wheel 沒進去，
# 驗證 pypsrp 失敗。可攜 Python／Node 已存在時 setup.sh 不會重解，只有 wheels 會更新。
if [ "$RUNTIME_READY" = 1 ] && { [ "$HAVE_RUNTIME_PACK" = 1 ] || [ "$HAVE_RUNTIME_PARTS" = 1 ]; }; then
  echo "  手上有 runtime 包 → 重新解開以更新離線 wheels（Python／Node 沿用已裝的）"
  RUNTIME_READY=0
fi

echo "  app 包　　　： ${APP_PACK:-（沒有）}"
if [ -n "$APP_PACK_IGNORED" ]; then
  echo "  └ 忽略較舊的： $APP_PACK_IGNORED"
fi
echo "  runtime 包　： $( [ "$HAVE_RUNTIME_PACK" = 1 ] && echo 有 || echo 沒有 )"
echo "  runtime 分割： $( [ "$HAVE_RUNTIME_PARTS" = 1 ] && echo "有 $(ls -1 $RT_PACK_GLOB.part[0-9][0-9] 2>/dev/null | wc -l) 份" || echo 沒有 )"
echo "  runtime 已裝： $( [ "$RUNTIME_READY" = 1 ] && echo "是（$RUNTIME，本次跳過）" || echo 否 )"

[ -n "$APP_PACK" ] || die \
    "找不到 app 包（$APP_PACK_GLOB）。" \
    "" \
    "app 包是每次都要的那一包（約 12 MB）。請確認它跟 install.sh 放在同一個目錄。"

if [ "$RUNTIME_READY" = 0 ] && [ "$HAVE_RUNTIME_PACK" = 0 ] && [ "$HAVE_RUNTIME_PARTS" = 0 ]; then
  die "這台還沒有執行環境，而手上也沒有 runtime 包。" \
      "" \
      "第一次安裝要同時有兩包：" \
      "  webit3-runtime-linux-x64-py311-node20.tar.gz（或它的 .part00/.part01/.part02）" \
      "  $APP_PACK" \
      "" \
      "（第二次以後升版才只需要 app 包 —— 那時 $RUNTIME 已經裝好了。）"
fi

# ---------------------------------------------------------------
step "校驗分割檔（每一份都到齊、沒壞）"
# ---------------------------------------------------------------
if [ "$RUNTIME_READY" = 1 ]; then
  echo "  runtime 已裝好 → 不需要 runtime 包，跳過這一步"
elif [ "$HAVE_RUNTIME_PARTS" = 1 ]; then
  _splits="$(ls -1 $RT_PACK_GLOB.splits.sha256 2>/dev/null | head -1 || true)"
  if [ -z "$_splits" ]; then
    echo "  ⚠ 沒有分割檔的 sha256 清單（*.splits.sha256）→ 無法逐份校驗"
    echo "    合併之後還是會驗整包，壞掉一定抓得到，只是抓不出是哪一份壞的"
  else
    echo "  清單： $_splits"
    if ! sha256sum -c "$_splits" > .splits.chk 2>&1; then
      cat .splits.chk | sed 's/^/    /'
      # 只取「檔名: FAILED…」那種行的檔名。sha256sum 另外會印
      # `sha256sum: <檔名>: No such file or directory`，那行的開頭是工具名不是檔名，
      # 照抓會把「sha256sum」當成壞掉的份報給使用者，所以先把它濾掉。
      _bad="$(grep -E '^[^:]+: (FAILED|沒有這個檔案)' .splits.chk \
              | grep -v '^sha256sum:' | sed 's/:.*//' | sort -u | tr '\n' ' ')"
      die "分割檔校驗沒過。" \
          "" \
          "有問題的份： $_bad" \
          "" \
          "處理方式 —— 只要重傳壞掉／缺少的那幾份，不必整包重來：" \
          "  · 顯示 FAILED　　　→ 那一份傳輸不完整或內容被改過，重傳它" \
          "  · 顯示找不到檔案　→ 那一份還沒傳過來，補傳它" \
          "重傳後把檔案放回 $WORK，再重跑： sudo bash $WORK/install.sh"
    fi
    cat .splits.chk | sed 's/^/    /'
    rm -f .splits.chk
    echo "  ✓ 每一份都在、而且完整"
  fi
else
  echo "  runtime 是未分割的整包 → 這一步不適用"
fi

# ---------------------------------------------------------------
step "合併 runtime 分割檔"
# ---------------------------------------------------------------
if [ "$RUNTIME_READY" = 1 ]; then
  echo "  runtime 已裝好 → 跳過"
elif [ "$HAVE_RUNTIME_PACK" = 1 ]; then
  echo "  已經是完整的一包，不用合併"
elif [ "$HAVE_RUNTIME_PARTS" = 1 ]; then
  _base="$(ls -1 $RT_PACK_GLOB.part[0-9][0-9] | head -1 | sed 's/\.part[0-9][0-9]$//')"
  _n="$(ls -1 "$_base".part[0-9][0-9] | wc -l)"
  echo "  合併 $_n 份 → $_base"
  # 後綴是補零的兩位數（part00、part01…），所以 shell 的字典序就是正確順序。
  # 沒補零的話（part0…part10）字典序會排成 0,1,10,2,… 合出來的檔案看起來正常、
  # 大小也對，只有解壓時才爆 —— 這就是為什麼下一步一定要驗整包 sha256。
  #
  # 用 part[0-9][0-9] 而不是 part*：後者會把 .parts.sha256 這種清單檔也 cat 進來。
  cat "$_base".part[0-9][0-9] > "$_base" \
      || die "合併失敗（寫不出 $_base）。" "磁碟空間不足？ 看： df -h /opt"
  echo "  ✓ 合併完成（$(wc -c < "$_base") bytes）"
  HAVE_RUNTIME_PACK=1
fi

# ---------------------------------------------------------------
step "校驗整包 sha256（過了才解壓）"
# ---------------------------------------------------------------
# 先驗再解，不是解了再說：合併順序錯、少一份、傳輸壞掉，這三件事只有整包的
# sha256 分辨得出來，而解壓的錯誤訊息一律長得像「包壞了」。
_verified=0
for _s in $(ls -1 *.sha256 2>/dev/null | grep -v '\.splits\.sha256$' || true); do
  # 清單裡指的檔案不在手上就略過（例：runtime 已裝好，沒傳 runtime 包）
  #
  # tr -d '\r' 不是裝飾：清單檔若是在 Windows 產生而帶了 CRLF，檔名會變成 "xxx.tar.gz\r"，
  # 於是 -f 判成不存在 → 整包校驗全部「略過」→ 看起來過了其實沒驗。
  # （2026-10-06 實際踩到；出包端也已改成一律寫 LF，這裡是第二層保險。）
  _target="$(awk '{print $2}' "$_s" | tr -d '\r' | sed 's/^\*//' | head -1)"
  [ -f "$_target" ] || { echo "  · 略過 $_s（$_target 不在這次安裝範圍）"; continue; }
  if sha256sum -c "$_s" > .whole.chk 2>&1; then
    cat .whole.chk | sed 's/^/    /'
    _verified=$((_verified+1))
  else
    cat .whole.chk | sed 's/^/    /'
    rm -f .whole.chk
    _sz="$(wc -c < "$_target")"
    die "整包校驗沒過： $_target（目前 $_sz bytes）" \
        "" \
        "三種可能，照這個順序判斷：" \
        "  1. 少了一份分割檔 → 合併後的大小會比正確值小。比對 ls -l 與交付清單的大小" \
        "  2. 分割檔順序錯了 → 大小對、sha256 不對（本包後綴補過零，正常不會發生）" \
        "  3. 傳輸過程壞了　 → 重傳。有 .splits.sha256 時先 sha256sum -c 它，找出是哪一份" \
        "" \
        "修好後重跑： sudo bash $WORK/install.sh"
  fi
  rm -f .whole.chk
done
if [ "$_verified" = 0 ]; then
  echo "  ⚠ 沒有可用的 sha256 清單檔 → 這次沒有校驗過"
  echo "    不擋安裝，但請知道：壞掉的包會在解壓或安裝途中才爆，比較難查"
else
  echo "  ✓ 已校驗 $_verified 個包"
fi

# ---------------------------------------------------------------
step "解開 runtime 包（可攜 Python / Node / 離線 wheels）"
# ---------------------------------------------------------------
if [ "$RUNTIME_READY" = 1 ]; then
  echo "  $RUNTIME 已經有可用的 Python 與 Node → 整段跳過"
  echo "    Python： $("$RUNTIME/python311/bin/python3" -V 2>&1)"
  echo "    Node　： $("$RUNTIME/node/bin/node" -v 2>&1)"
  echo "  （這就是拆兩包的目的：升版只要傳 app 包。）"
else
  _rt="$(ls -1 $RT_PACK_GLOB 2>/dev/null | grep -v '\.part' | head -1 || true)"
  [ -n "$_rt" ] || die "找不到可用的 runtime 包。" "合併那一步可能沒成功，請看上面的訊息。"
  mkdir -p "$RT_SRC" || die "建不出 $RT_SRC"
  chmod 700 "$RT_SRC" 2>/dev/null || true
  find "$RT_SRC" -mindepth 1 -delete 2>/dev/null || true   # 清掉舊素材（含舊的 REQ_SHA256），避免殘檔造成誤判
  echo "  解開 $_rt → $RT_SRC"
  tar xzf "$_rt" -C "$RT_SRC" || die "解開 runtime 包失敗。" \
      "" \
      "可能原因：" \
      "  · 磁碟空間不足　　→ df -h /opt" \
      "  · 包本身損壞　　　→ 上一步的 sha256 若是略過的，請補上清單檔重驗" \
      "  · 缺 xz（包內 Node 是 .tar.xz）→ command -v xz"
  echo "  ✓ runtime 素材已就位："
  ls -1 "$RT_SRC" | sed 's/^/    /'
fi

# ---------------------------------------------------------------
step "解開 app 包"
# ---------------------------------------------------------------
APP_SRC="$WORK/app-src"
rm -rf "$APP_SRC"
mkdir -p "$APP_SRC" || die "建不出 $APP_SRC"
# app 包也要驗 sha256。
# 2026-10-13：原本只有 runtime 包驗（上面那段），app 包的 .sha256 就放在旁邊
# 卻從來沒被讀過，只在解壓失敗的錯誤訊息裡叫人「補上 .sha256 重驗」。
# 傳壞的 app 包會一路裝完，到執行期才炸。
_app_sha="${APP_PACK}.sha256"
if [ -f "$_app_sha" ]; then
  if sha256sum -c "$_app_sha" > .app.chk 2>&1; then
    echo "  sha256　　　： ✓ 一致"
    rm -f .app.chk
  else
    sed 's/^/    /' .app.chk
    rm -f .app.chk
    die "app 包 sha256 不符，不往下裝。" \
        "" \
        "包： $APP_PACK" \
        "" \
        "傳輸過程壞了。請重傳 $APP_PACK 與 ${APP_PACK}.sha256 兩個檔。"
  fi
else
  echo "  sha256　　　： ⚠ 沒有 ${APP_PACK}.sha256，這包沒驗過"
fi

echo "  解開 $APP_PACK → $APP_SRC"
tar xzf "$APP_PACK" -C "$APP_SRC" || die "解開 app 包失敗。" \
    "磁碟空間不足（df -h /opt）或包損壞（補上 ${APP_PACK}.sha256 重驗）。"
[ -f "$APP_SRC/setup.sh" ] || die \
    "app 包裡找不到 setup.sh。" \
    "" \
    "這包的內容不對 —— 可能拿到的是 runtime 包或別的檔案。" \
    "目前解出來的東西：" \
    "$(ls -1 "$APP_SRC" | head -20 | sed 's/^/  /')"
echo "  ✓ 解開完成"

# ---------------------------------------------------------------
step "檢查這包跟 runtime 的 wheels 對不對得上"
# ---------------------------------------------------------------
# app 包與 runtime 包各帶一份 requirements 的指紋（REQ_SHA256）。不一致＝app 需要的
# 套件在 runtime 的離線 wheels 裡找不到，pip 離線安裝一定失敗（或像 11-16 那樣
# 悄悄少裝 pypsrp）。在動任何東西之前就擋下。
_app_req="$(tr -d ' \t\r\n' < "$APP_SRC/REQ_SHA256" 2>/dev/null || true)"
_rt_req="$(tr -d ' \t\r\n' < "$RT_SRC/REQ_SHA256" 2>/dev/null || true)"
if [ -z "$_app_req" ]; then
  echo "  ⚠ app 包沒有 REQ_SHA256（舊格式的包）→ 這一關驗不了"
elif [ "$_app_req" = "$_rt_req" ]; then
  echo "  ✓ 一致（${_app_req:0:12}…）"
elif [ -z "$_rt_req" ]; then
  echo "  ⚠ 這台的 runtime 是舊格式（沒有 REQ_SHA256），驗不了 wheels 對不對得上。"
  echo "    若這次新增了 Python 套件（例如 pypsrp），請連同新版 runtime 包一起放進 $WORK 再裝。"
else
  die "app 包需要的套件清單，跟這台 runtime 的離線 wheels 對不上。" \
      "" \
      "  app 包指紋　　： ${_app_req:0:16}" \
      "  runtime 指紋　： ${_rt_req:-（沒有，是舊版 runtime）}" \
      "" \
      "請把『同一次出包』的 runtime 包（webit3-runtime-*.tar.gz 或 .part00～）一起放進 $WORK 再重跑。" \
      "（什麼都還沒動，舊版照常運作。）"
fi

# ---------------------------------------------------------------
step "偵測是否為重裝、備份舊版"
# ---------------------------------------------------------------
_pack_ver="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$APP_SRC/backend/version.json" 2>/dev/null | head -1)"
PACK_VER="$_pack_ver"
echo "  這包的版本： ${PACK_VER:-未知}"
if [ -f "$APP_DIR/backend/version.json" ] && [ -f "$DATA_DIR/install.conf" ]; then
  [ -n "$PACK_VER" ] || die "讀不到這包的版號（backend/version.json），無法安全重裝。" "包可能不完整，請重傳 app 包。（什麼都還沒動。）"
  REINSTALL=1
  OLD_VER="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$APP_DIR/backend/version.json" 2>/dev/null | head -1)"
  echo "  偵測到已安裝的版本： ${OLD_VER:-未知} → 重裝模式（沿用 $DATA_DIR/install.conf，不再問 IP／帳號／密碼）"
  # 不准默默降版：舊版比新包還新，通常是拿錯包。
  if [ -n "$OLD_VER" ] && [ -n "$PACK_VER" ] && [ "$OLD_VER" != "$PACK_VER" ] \
     && [ "$(printf '%s\n%s\n' "$OLD_VER" "$PACK_VER" | sort -V | tail -1)" = "$OLD_VER" ] \
     && [ "${WEBIT_ALLOW_DOWNGRADE:-0}" != "1" ]; then
    die "這台已經是 $OLD_VER，比這包（$PACK_VER）還新 —— 疑似拿錯包。" \
        "" \
        "真的要降版請加： sudo WEBIT_ALLOW_DOWNGRADE=1 bash $WORK/install.sh" \
        "（什麼都還沒動。）"
  fi
  # 磁碟空間：備份＋新程式至少要 1 GB
  _free_kb="$(df -Pk /opt 2>/dev/null | awk 'NR==2{print $4}')"
  if [ -n "$_free_kb" ] && [ "$_free_kb" -lt 1048576 ]; then
    die "/opt 剩餘空間不到 1 GB（$((_free_kb/1024)) MB），不夠備份與安裝。" "先清出空間再重跑（df -h /opt）。"
  fi
  BK_DIR="$WORK/backup_$(date +%Y%m%d_%H%M%S)_v${OLD_VER:-unknown}"   # 在 root 專屬工作區，服務帳號改不到
  mkdir -p "$BK_DIR" || die "建不出備份目錄 $BK_DIR"
  chmod 700 "$BK_DIR"
  # 先停服務再備份：備份之後到新版啟動之前沒有任何寫入，還原才不會丟資料，DB 備份也是靜止狀態。
  echo "  停止服務（webit3-web／webit3-api）以取得一致的備份…"
  systemctl stop webit3-web webit3-api 2>/dev/null || true
  SVC_STOPPED=1
  _bk_items="backend frontend"; [ -f "$APP_DIR/deploy.sh" ] && _bk_items="$_bk_items deploy.sh"
  tar czf "$BK_DIR/app.tar.gz" -C "$APP_DIR" $_bk_items 2>/dev/null \
      || die "備份舊版 app 失敗（$BK_DIR/app.tar.gz）。" "什麼都還沒動；先看 df -h /opt。"
  # asset.db：用 SQLite 備份 API 取一致性副本（服務還在跑，直接 cp 可能抓到寫到一半）。
  if [ -f "$DATA_DIR/asset.db" ]; then
    _py="$VENV_DIR/bin/python"; [ -x "$_py" ] || _py="$RUNTIME/python311/bin/python3"
    if [ -x "$_py" ] && "$_py" - "$DATA_DIR/asset.db" "$BK_DIR/asset.db" <<'PYEOF'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()
PYEOF
    then
      echo "  ✓ asset.db 一致性備份 → $BK_DIR/asset.db（$(wc -c < "$BK_DIR/asset.db") bytes）"
    else
      cp -f "$DATA_DIR/asset.db" "$BK_DIR/asset.db" || die "備份 asset.db 失敗。" "什麼都還沒動。"
      cp -f "$DATA_DIR/asset.db-wal" "$BK_DIR/" 2>/dev/null || true; cp -f "$DATA_DIR/asset.db-shm" "$BK_DIR/" 2>/dev/null || true
      echo "  ✓ asset.db 已複製備份（SQLite 備份 API 不可用，改用檔案複製）"
    fi
  fi
  cp -f "$DATA_DIR/install.conf" "$BK_DIR/install.conf" 2>/dev/null || true
  cp -f /etc/systemd/system/webit3-*.service "$BK_DIR/" 2>/dev/null || true
  tar tzf "$BK_DIR/app.tar.gz" >/dev/null 2>&1 && tar tzf "$BK_DIR/app.tar.gz" | grep -q '^backend/version.json$'       || die "備份的 app.tar.gz 驗證失敗（讀不回來或缺 backend/version.json）。" "什麼都還沒改，舊版會重新啟動。"
  BK_OK=1
  echo "  ✓ 舊版 app 備份 → $BK_DIR/app.tar.gz（$(wc -c < "$BK_DIR/app.tar.gz") bytes）"
  echo "  備份目錄＝ $BK_DIR　（失敗會自動還原；裝完確認沒事再自己刪）"
else
  echo "  沒有舊版（或沒有 install.conf）→ 全新安裝模式（會問設定與 admin 密碼）"
fi

# ---------------------------------------------------------------
step "執行 setup.sh$( [ "$REINSTALL" = 1 ] && echo '（重裝：沿用舊設定）' || echo '（接下來會問幾個設定）' )"
# ---------------------------------------------------------------
if [ "$REINSTALL" = 1 ]; then
  echo "  沿用： $DATA_DIR/install.conf 的 IP、帳號、埠；admin 帳號已存在就不重設密碼。"
  echo "  資料（asset.db、收集金鑰、設定）不會被覆蓋。"
else
  echo "  它會問：對外服務 IP、跑服務的系統帳號、埠有沒有被佔用，最後要你設 admin 密碼。"
fi
echo "  安裝路徑沿用既有預設： /opt/webit3/{app,data,venv,runtime}"
echo
INSTALL_STARTED=1
export WEBIT_REINSTALL="$REINSTALL"
# WEBIT_RT_SRC 告訴 setup.sh「可攜 Python/Node/wheels 的 tarball 在哪」——
# 兩包拆分之後它們不再跟 setup.sh 同一個目錄了。
WEBIT_RT_SRC="$RT_SRC" bash "$APP_SRC/setup.sh"
_rc=$?
[ "$_rc" = 0 ] || die \
    "setup.sh 以離開碼 $_rc 結束。" \
    "" \
    "它自己會印出完整 log 的路徑（/opt/webit3/data/logs/setup_*.log）。" \
    "先看最後 50 行： tail -50 /opt/webit3/data/logs/setup_*.log | tail -60" \
    "修好之後重跑這支就好（冪等）： sudo bash $WORK/install.sh"

# ---------------------------------------------------------------
step "驗收"
# ---------------------------------------------------------------
CONF="${WEBIT_DATA:-/opt/webit3/data}/install.conf"
API_HOST=""; API_PORT="8000"; WEB_PORT="3000"
if [ -f "$CONF" ]; then
  # install.conf 是 setup.sh 寫的 KEY=VALUE，只取需要的三個值，不 source 整份檔。
  API_HOST="$(sed -n 's/^API_HOST=//p' "$CONF" | head -1)"
  API_PORT="$(sed -n 's/^API_PORT=//p' "$CONF" | head -1)"
  WEB_PORT="$(sed -n 's/^WEB_PORT=//p' "$CONF" | head -1)"
  WEBIT_HTTPS="$(sed -n 's/^WEBIT_HTTPS=//p' "$CONF" | head -1)"
fi
API_PORT="${API_PORT:-8000}"; WEB_PORT="${WEB_PORT:-3000}"

echo "  — 服務狀態 —"
for s in webit3-api webit3-web; do
  printf '    %-14s %s\n' "$s" "$(systemctl is-active "$s" 2>/dev/null || echo unknown)"
done
echo "  — 排程 —"
systemctl list-timers 'webit3-*' --no-pager 2>/dev/null | sed 's/^/    /' | head -6

echo "  — /api/version（唯一免登入的端點）—"
if command -v curl >/dev/null 2>&1; then
  curl -s --max-time 10 "http://127.0.0.1:${API_PORT}/api/version" | sed 's/^/    /' || true
  echo
else
  echo "    （這台沒有 curl，請自行用瀏覽器開 http://${API_HOST:-<正式機位址>}:${API_PORT}/api/version）"
fi

echo "  — 埠（listen）—"
ss -tln 2>/dev/null | grep -E "[:.](${API_PORT}|${WEB_PORT})[[:space:]]" | sed 's/^/    /' \
  || echo "    ⚠ 沒看到 ${API_PORT}/${WEB_PORT} 在 listen"

# 防火牆：印回查到的實際值，不印「已開」這種宣告。
# 開埠失敗**不算安裝失敗** —— 服務本身已經裝好也起來了（使用者 2026-10-06：
# 「網路我會申請，OS 你搞定，不要防火牆開通卡死自己」）。
echo "  — 防火牆（入站）—"
FW_LINE=""
if ! command -v firewall-cmd >/dev/null 2>&1; then
  FW_LINE="未開（這台沒有 firewall-cmd）"
  echo "    firewalld: 沒有 firewall-cmd"
elif ! systemctl is-active --quiet firewalld; then
  FW_LINE="未開（firewalld 未啟用）"
  echo "    firewalld: inactive（未開埠）"
else
  _fwp="$(firewall-cmd --list-ports 2>/dev/null || true)"
  echo "    firewalld: active"
  echo "    已開放埠: ${_fwp:-（空）}   ← firewall-cmd --list-ports 回查的實際值"
  _m=""
  if [ "${WEBIT_HTTPS:-0}" = "1" ]; then
    case " $_fwp " in *" 443/tcp "*) : ;; *) _m=" 443/tcp" ;; esac
    if [ -z "$_m" ]; then FW_LINE="已開 443/tcp"; else FW_LINE="未開（回查不到:$_m）"; fi
  else
    case " $_fwp " in *" ${API_PORT}/tcp "*) : ;; *) _m="$_m ${API_PORT}/tcp" ;; esac
    case " $_fwp " in *" ${WEB_PORT}/tcp "*) : ;; *) _m="$_m ${WEB_PORT}/tcp" ;; esac
    if [ -z "$_m" ]; then FW_LINE="已開 ${WEB_PORT}/tcp ${API_PORT}/tcp"
    else FW_LINE="未開（回查不到:$_m）"; fi
  fi
fi

_ok=1
systemctl is-active --quiet webit3-api || _ok=0
systemctl is-active --quiet webit3-web || _ok=0

# ---- 自驗：裝進去的真的是這一包嗎？（不是「指令都回 0」就算過）----
# 起因 2026-11-16：正式機跑了安裝，畫面每一步都成功，但裝完仍是舊的 1.415.0。
echo "  — 自驗 —"
VERIFY_FAIL=""
[ "$_ok" = 1 ] || VERIFY_FAIL=" webit3-api／webit3-web 有服務不是 active;"
_disk_ver="$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$APP_DIR/backend/version.json" 2>/dev/null | head -1)"
if [ -n "${PACK_VER:-}" ] && [ "$_disk_ver" = "$PACK_VER" ]; then
  echo "    ✓ 磁碟上的 version.json ＝ $_disk_ver（跟這包一致）"
else
  VERIFY_FAIL="$VERIFY_FAIL 磁碟版本=${_disk_ver:-讀不到}（應為 ${PACK_VER:-未知}）;"
fi
if command -v curl >/dev/null 2>&1; then
  _api_ver=""
  for _i in 1 2 3 4 5 6 7 8 9 10 11 12; do      # 後端冷啟動最多等約 36 秒
    _api_ver="$(curl -s --max-time 5 "http://127.0.0.1:${API_PORT}/api/version" | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' | head -1)"
    [ -n "$_api_ver" ] && break
    sleep 3
  done
  if [ -n "${PACK_VER:-}" ] && [ "$_api_ver" = "$PACK_VER" ]; then
    echo "    ✓ /api/version 回報 $_api_ver（跑的就是新碼）"
  else
    VERIFY_FAIL="$VERIFY_FAIL /api/version=${_api_ver:-沒回應}（應為 ${PACK_VER:-未知}）;"
  fi
  if [ "${WEBIT_HTTPS:-0}" = "1" ]; then
    # 443 模式：上面直打本機埠繞過了 nginx，這裡走 nginx 再驗一次（自簽憑證用 -k）
    _hv="$(curl -sk --max-time 10 "https://127.0.0.1/api/version" | sed -n 's/.*"version": *"\([^"]*\)".*//p' | head -1)"
    if [ -n "${PACK_VER:-}" ] && [ "$_hv" = "$PACK_VER" ]; then
      echo "    ✓ https://127.0.0.1/api/version（經 nginx 443）→ $_hv"
    else
      VERIFY_FAIL="$VERIFY_FAIL 經 nginx 443 的 /api/version=${_hv:-沒回應};"
    fi
    _hc="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 15 "https://127.0.0.1/v2" 2>/dev/null || echo 000)"
    case "$_hc" in 2??|3??|401|403) echo "    ✓ https://127.0.0.1/v2 → HTTP $_hc" ;; *) VERIFY_FAIL="$VERIFY_FAIL 經 nginx 443 的 /v2=HTTP $_hc;" ;; esac
  fi
  for _pg in / /v2; do
    _code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "http://127.0.0.1:${WEB_PORT}${_pg}" 2>/dev/null || echo 000)"
    case "$_code" in
      2??|3??|401|403) echo "    ✓ 前端 ${_pg} → HTTP $_code（有回應）" ;;
      *)       VERIFY_FAIL="$VERIFY_FAIL 前端 ${_pg}=HTTP ${_code};" ;;
    esac
  done
else
  echo "    ⚠ 這台沒有 curl，跳過 API／前端自驗（請自行確認版本與 /v2）"
fi
if [ -n "$VERIFY_FAIL" ]; then
  _ok=0
  echo "    ✗ 自驗沒過：$VERIFY_FAIL"
  if [ "$REINSTALL" = 1 ] && [ "$ROLLED_BACK" = 0 ]; then
    rollback_install
    echo
    echo "⚠️ 新版自驗沒過，已自動還原到 ${OLD_VER:-舊版}。這包先不要用，把這份畫面與 log 交給 AI。"
    exit 1
  fi
fi

echo
echo "════════════════════════════════════════════════════════════"
if [ "$_ok" = 1 ]; then
  echo "✅ 安裝完成　$(date '+%F %T')"
  echo
  if [ "${WEBIT_HTTPS:-0}" = "1" ]; then echo "   請用瀏覽器打開：  https://${API_HOST:-<正式機位址>}   （自簽憑證，瀏覽器會提示不信任）"; else echo "   請用瀏覽器打開：  http://${API_HOST:-<正式機位址>}:${WEB_PORT}"; fi
  echo "   用剛剛設定的 admin 帳密登入。"
  echo
  case "$FW_LINE" in
    已開*) echo "   防火牆: $FW_LINE" ;;
    *)     echo "   防火牆: $FW_LINE — 服務已裝好並啟動，但別台可能連不進來。"
           echo "           需要開埠時執行："
           if [ "${WEBIT_HTTPS:-0}" = "1" ]; then
             echo "             sudo firewall-cmd --permanent --add-port=443/tcp"
           else
             echo "             sudo firewall-cmd --permanent --add-port=${WEB_PORT}/tcp"
             echo "             sudo firewall-cmd --permanent --add-port=${API_PORT}/tcp"
           fi
           echo "             sudo firewall-cmd --reload" ;;
  esac
  echo "   （以上是入站。出站 22／5985／掃描埠屬網段層級，須另向網路單位申請。）"
else
  echo "⚠️ 安裝跑完了，但有服務不是 active —— 先不要宣稱成功。"
  echo
  echo "   查原因（真正的錯誤在 journal 裡，不在 systemctl status）："
  echo "     journalctl -u webit3-api -n 60 --no-pager"
  echo "     journalctl -u webit3-web -n 60 --no-pager"
  echo "   最常見的三個原因：埠被佔用、SELinux 擋、權限不對。"
  echo "   排查步驟見交付文件「出問題再看」那一章。"
fi
echo
echo "   這次的完整 log： ${WEBIT_DATA:-/opt/webit3/data}/logs/setup_*.log（最新那一份）"
echo "   安裝工作區　　： $WORK"
echo "════════════════════════════════════════════════════════════"
[ "$_ok" = 1 ] || exit 1
