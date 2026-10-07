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
  exit 1
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
APP_PACK="$(ls -1 $APP_PACK_GLOB 2>/dev/null | head -1 || true)"

RUNTIME_READY=0
if [ -x "$RUNTIME/python311/bin/python3" ] && [ -x "$RUNTIME/node/bin/node" ]; then
  RUNTIME_READY=1
fi

echo "  app 包　　　： ${APP_PACK:-（沒有）}"
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
step "執行 setup.sh（接下來會問幾個設定）"
# ---------------------------------------------------------------
echo "  它會問：對外服務 IP、跑服務的系統帳號、埠有沒有被佔用，最後要你設 admin 密碼。"
echo "  安裝路徑沿用既有預設： /opt/webit3/{app,data,venv,runtime}"
echo
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
  case " $_fwp " in *" ${API_PORT}/tcp "*) : ;; *) _m="$_m ${API_PORT}/tcp" ;; esac
  case " $_fwp " in *" ${WEB_PORT}/tcp "*) : ;; *) _m="$_m ${WEB_PORT}/tcp" ;; esac
  if [ -z "$_m" ]; then FW_LINE="已開 ${WEB_PORT}/tcp ${API_PORT}/tcp"
  else FW_LINE="未開（回查不到:$_m）"; fi
fi

_ok=1
systemctl is-active --quiet webit3-api || _ok=0
systemctl is-active --quiet webit3-web || _ok=0

echo
echo "════════════════════════════════════════════════════════════"
if [ "$_ok" = 1 ]; then
  echo "✅ 安裝完成　$(date '+%F %T')"
  echo
  echo "   請用瀏覽器打開：  http://${API_HOST:-<正式機位址>}:${WEB_PORT}"
  echo "   用剛剛設定的 admin 帳密登入。"
  echo
  case "$FW_LINE" in
    已開*) echo "   防火牆: $FW_LINE" ;;
    *)     echo "   防火牆: $FW_LINE — 服務已裝好並啟動，但別台可能連不進來。"
           echo "           需要開埠時執行："
           echo "             sudo firewall-cmd --permanent --add-port=${WEB_PORT}/tcp"
           echo "             sudo firewall-cmd --permanent --add-port=${API_PORT}/tcp"
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
