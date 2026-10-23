#!/bin/sh
# ============================================================================
# ⚠️ 這個檔案是**產生出來的**，不要直接改它。
#     改 run_auto_web3.sh / install.sh / push_agent.sh，然後跑：
#         python backend/agent_scripts/build_bootstrap.py
#     測試會驗這份跟三支正本同步（test_agent_scripts_security.py）。
# ============================================================================
#
# Push agent watcher 一次性佈署腳本——root 登入後只要這一行：
#
#     sudo sh bootstrap_watcher.sh
#
# 這是唯一需要 root/PAM 代登的步驟，執行完可以立刻結束那次 root session、
# 刪掉這支腳本。之後 watcher 自己每天用 root crontab 跑，日常 agent 資料回報
# 走 sysinfra 的 crontab，兩邊都不需要人再登入。
#
# 全程純文字可讀：沒有 base64、沒有下載、沒有 ExecutionPolicy 之類的旁路。
# 貼上去執行的人看得懂自己在跑什麼，稽核也查得到。
# ============================================================================

set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "bootstrap_watcher.sh 需要 root 權限執行（sudo sh bootstrap_watcher.sh）" >&2
    exit 1
fi

AGENT_USER="sysinfra"
BIN_DIR="/opt/webit3-agent/bin"
INCOMING_DIR="/var/lib/webit3-agent/incoming"
LOG_DIR="/caslog/webit3_agent"

mkdir -p "$BIN_DIR" "$LOG_DIR/done" "$LOG_DIR/failed" "$LOG_DIR/archive"

# ---- 投放目錄：0730 root:sysinfra ----
# 這是這次佈署最重要的一行。舊版讓 root 排程去撿 /tmp/webit3_agent_* 執行，
# 而 /tmp 全域可寫 → 任何本機帳號都能讓 root 幫他安裝任意程式（真的提權，
# 不是外觀問題，說明見 install.sh 開頭）。
#   owner root  ：只有 root 能讀取內容、決定要不要處理
#   group 上傳帳號：wx——能寫入、能進入自己知道名字的目錄，但不能列出目錄
#   other 0     ：其他本機帳號完全碰不到
if ! id "$AGENT_USER" >/dev/null 2>&1; then
    echo "找不到 $AGENT_USER 帳號——請先建立收集帳號再跑這支腳本" >&2
    exit 1
fi
mkdir -p "$INCOMING_DIR"
chown "root:$AGENT_USER" "$INCOMING_DIR"
chmod 730 "$INCOMING_DIR"

# ---- run_auto_web3.sh ----
cat > "$BIN_DIR/run_auto_web3.sh" <<'RUN_AUTO_WEB3_EOF'
#!/bin/sh
# root 排程 watcher 本體。由 root 的 crontab 每天觸發一次（見 bootstrap_watcher.sh
# 註冊的排程），掃描投放目錄底下已上傳完整（有 .ready 標記）的任務，
# 通過資源檢查閘門＋lockfile 防重疊之後，逐一執行 install.sh 落地。
#
# 設計原則（2026-08-14 定案，詳見 backend/agent_scripts/README.md）：
# - 不是即時系統，一天掃一次就夠，安裝 agent 這件事完全不急。
# - 資源檢查沒過就跳過這輪，留給明天重試，不強跑；連續跳過太多天要留痕跡讓人知道。
# - lockfile 用 PID 存活判斷是否重疊執行，不是用時間猜測——正確處理「上次意外中斷、
#   沒清乾淨鎖檔」的情況。
#
# ⚠️ 2026-08-28：投放目錄從 `/tmp/webit3_agent_*` 搬到 $INCOMING_DIR。
# `/tmp` 是全域可寫，root 到那裡撿東西執行＝**任何本機帳號都能讓 root 幫他跑程式**。
# 那是真的本機提權漏洞，不是外觀問題；完整說明寫在 install.sh 開頭。

set -eu

BASE_DIR="/opt/webit3-agent"
INCOMING_DIR="/var/lib/webit3-agent/incoming"
LOG_DIR="/caslog/webit3_agent"
LOCK_FILE="$LOG_DIR/.run_auto_web3.lock"
SKIP_COUNT_FILE="$LOG_DIR/.skip_count"
INSTALL_SH="$BASE_DIR/bin/install.sh"

MAX_LOAD_RATIO="0.8"    # CPU 1分鐘負載平均 / 核心數，超過就算忙
MIN_MEM_FREE_PCT="5"    # 可用記憶體 % 低於這個就算忙（裝 agent 很輕量，門檻寬鬆）
MAX_DISK_USED_PCT="85"  # / 或 /caslog 使用率超過這個就算忙
SKIP_ALERT_THRESHOLD="3"   # 連續跳過幾天要寫警示日誌
RETENTION_DAYS="30"        # done/failed 保留幾天，超過壓縮進 archive/

mkdir -p "$LOG_DIR/done" "$LOG_DIR/failed" "$LOG_DIR/archive"
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >> "$LOG_DIR/run_auto_web3.log"; }

# ---- 1. lockfile 防重疊執行 ----
if [ -f "$LOCK_FILE" ]; then
    OLD_PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        log "上一輪（PID $OLD_PID）還在跑，這輪跳過，不重疊執行"
        exit 0
    fi
    log "發現殘留 lockfile（PID $OLD_PID 已不存在，上次應該是意外中斷），清掉重新執行"
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# ---- 2. 資源檢查閘門 ----
resource_busy() {
    NPROC=$(nproc 2>/dev/null || echo 1)
    LOAD1=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || echo 0)
    LOAD_RATIO=$(awk -v l="$LOAD1" -v n="$NPROC" 'BEGIN { printf "%.2f", l/n }')
    if awk -v r="$LOAD_RATIO" -v m="$MAX_LOAD_RATIO" 'BEGIN { exit !(r >= m) }'; then
        log "CPU 忙（負載/核心=$LOAD_RATIO >= $MAX_LOAD_RATIO），跳過這輪"
        return 0
    fi

    MEM_FREE_PCT=$(free 2>/dev/null | awk '/^Mem:/ { printf "%.1f", $7/$2*100 }')
    if [ -n "$MEM_FREE_PCT" ] && awk -v f="$MEM_FREE_PCT" -v m="$MIN_MEM_FREE_PCT" 'BEGIN { exit !(f < m) }'; then
        log "記憶體忙（可用=$MEM_FREE_PCT% < $MIN_MEM_FREE_PCT%），跳過這輪"
        return 0
    fi

    for MOUNT in / "$LOG_DIR"; do
        USED_PCT=$(df -P "$MOUNT" 2>/dev/null | awk 'NR==2 { gsub("%","",$5); print $5 }')
        if [ -n "$USED_PCT" ] && [ "$USED_PCT" -ge "$MAX_DISK_USED_PCT" ] 2>/dev/null; then
            log "磁碟忙（$MOUNT 使用率=$USED_PCT% >= $MAX_DISK_USED_PCT%），跳過這輪"
            return 0
        fi
    done
    return 1
}

if resource_busy; then
    SKIP_COUNT=$(( $(cat "$SKIP_COUNT_FILE" 2>/dev/null || echo 0) + 1 ))
    echo "$SKIP_COUNT" > "$SKIP_COUNT_FILE"
    if [ "$SKIP_COUNT" -ge "$SKIP_ALERT_THRESHOLD" ]; then
        log "⚠️ 已連續跳過 $SKIP_COUNT 天，這台主機資源可能長期不足，需要人工判斷是否介入"
    fi
    exit 0
fi
rm -f "$SKIP_COUNT_FILE"

# ---- 3. 掃描 /tmp 底下已就緒的任務，逐一落地 ----
if [ ! -x "$INSTALL_SH" ]; then
    log "找不到 $INSTALL_SH（watcher 佈署可能不完整），無法執行任何任務"
    exit 1
fi

# 掃之前先確認投放目錄本身沒被動過。這是**整輪的前提**：目錄權限一旦鬆掉，
# 底下每一個任務的檢查都失去意義，所以寧可整輪不做，也不要挑幾個看起來沒問題的做。
# 期望：0730 root:sysinfra——sysinfra 能寫入與進入，其他本機帳號完全碰不到。
if [ ! -d "$INCOMING_DIR" ]; then
    log "投放目錄 $INCOMING_DIR 不存在（佈署不完整或被刪），這輪沒有任務可做"
    exit 0
fi
INC_STAT=$(stat -c '%U:%a' "$INCOMING_DIR" 2>/dev/null || echo "unknown")
case "$INC_STAT" in
    root:730|root:700|root:750) ;;
    *)
        log "⚠️ 拒絕整輪：$INCOMING_DIR 權限是 $INC_STAT，不是預期的 root:730。" \
            "權限被放寬代表這個目錄可能已經不受控，這輪一個任務都不執行，請人工查看"
        exit 1
        ;;
esac

for TASK_DIR in "$INCOMING_DIR"/*; do
    [ -d "$TASK_DIR" ] || continue
    [ -L "$TASK_DIR" ] && continue
    [ -f "$TASK_DIR/.ready" ] || continue

    NAME=$(basename "$TASK_DIR")
    if "$INSTALL_SH" "$TASK_DIR" >> "$LOG_DIR/done/$NAME.log" 2>&1; then
        log "$NAME 安裝成功"
    else
        log "$NAME 安裝失敗，log 見 $LOG_DIR/failed/$NAME.log"
        mv "$LOG_DIR/done/$NAME.log" "$LOG_DIR/failed/$NAME.log" 2>/dev/null || true
        rm -rf "$TASK_DIR" 2>/dev/null || true
    fi
done

# ---- 4. housekeeping：過保留期的 done/failed 壓縮進 archive/ ----
find "$LOG_DIR/done" "$LOG_DIR/failed" -maxdepth 1 -type f -mtime "+$RETENTION_DAYS" 2>/dev/null | \
    while read -r OLD_LOG; do
        gzip -c "$OLD_LOG" > "$LOG_DIR/archive/$(basename "$OLD_LOG").gz" 2>/dev/null && rm -f "$OLD_LOG"
    done
RUN_AUTO_WEB3_EOF

# ---- install.sh ----
cat > "$BIN_DIR/install.sh" <<'INSTALL_SH_EOF'
#!/bin/sh
# Push agent「落地」腳本，由 root 排程 watcher（`run_auto_web3.sh`）在通過資源檢查
# 閘門＋lockfile 防重疊之後執行。
#
# 用法：install.sh <task_dir>
#   task_dir 必須位於 $INCOMING_DIR 底下，**只放資料，不放程式碼**：
#     agent_key       — 這台主機的 agent key（落地後存 /etc/webit3-agent/key，600）
#     collector_url   — collector 的 URL
#     .ready          — 標記檔，代表上傳完整
#
# ============================================================================
# ⚠️ 2026-08-28 重寫：原本的設計是**真的本機提權漏洞**，不是外觀問題
# ============================================================================
# 舊版做了兩件致命的事：
#   1. 掃 `/tmp/webit3_agent_*`——`/tmp` 是全域可寫
#   2. 從那個目錄拿 `push_agent.sh`，用 root 裝到 /opt 並註冊進 crontab
#
# 合起來的後果：**這台機器上任何一個本機帳號**都能自己建
# `/tmp/webit3_agent_x/`，塞一份自己寫的 `push_agent.sh` 加上 `.ready`，
# 隔天 root 排程就會幫他把那份程式裝進 /opt 755、排進 sysinfra 的每日 crontab。
# 而 sysinfra 這個帳號持有全機隊的 SSH 金鑰 → 一個本機低權帳號直接變成
# 橫向移動的跳板。不需要任何漏洞利用技巧，照著我們自己的設計走就成立。
#
# 修法就是天條那一句：**root 永遠不執行「來路可控」的程式碼。
# 外部只能傳資料，不能傳可執行內容。**
#   · push_agent.sh 改成**隨 bootstrap 一起固定佈署**在 $BIN_DIR，由這支複製過去；
#     task_dir 裡出現 push_agent.sh 一律視為攻擊跡象，整個任務拒絕並留痕
#   · 投放目錄搬離 /tmp，改用 $INCOMING_DIR（0730 root:sysinfra，其他人完全進不去）
#   · 剩下兩個檔案是純資料，逐一驗格式；不合就拒絕，不試著修正
#
# 這支需要 root 是因為要寫 /etc、/opt；但日常真正執行 push_agent.sh 的排程註冊在
# sysinfra 的 crontab，不是 root 的——裝的當下才用到 root，之後每天在跑的權限最小化。

set -eu

TASK_DIR="${1:?用法：install.sh <task_dir>}"
AGENT_USER="sysinfra"
CONF_DIR="/etc/webit3-agent"
INSTALL_DIR="/opt/webit3-agent"
BIN_DIR="$INSTALL_DIR/bin"
INCOMING_DIR="/var/lib/webit3-agent/incoming"
LOG_DIR="/caslog/webit3_agent"

# 程式碼的唯一來源：隨佈署固定下來的這一份，不是 task_dir 傳上來的
SRC_PUSH_AGENT="$BIN_DIR/push_agent.sh"

MAX_FILE_BYTES=4096     # 兩個檔案都只有一行字串，4K 綽綽有餘；超過就是不對勁

if [ "$(id -u)" -ne 0 ]; then
    echo "install.sh 需要 root 權限" >&2
    exit 1
fi

# 拒絕的理由要能一眼分辨是「上傳不完整」還是「疑似被塞東西」——兩者處理方式不同：
# 前者等下次重傳就好，後者要有人去看那台機器發生了什麼事，所以後者一律加 ⚠️。
reject() {
    echo "$*" >&2
    exit 1
}

# ---- 1. 路徑必須在投放目錄底下，且不是符號連結 ----
# 少了這段，攻擊者只要讓 watcher 看到一個 symlink，就能把 root 的 rm -rf 指到任意路徑。
case "$TASK_DIR" in
    "$INCOMING_DIR"/*) ;;
    *) reject "拒絕：$TASK_DIR 不在 $INCOMING_DIR 底下" ;;
esac
[ -L "$TASK_DIR" ] && reject "⚠️ 拒絕：$TASK_DIR 是符號連結（疑似被塞東西），請人工查看"
[ -d "$TASK_DIR" ] || reject "拒絕：$TASK_DIR 不是目錄"
REAL_DIR=$(readlink -f "$TASK_DIR" 2>/dev/null || echo "")
case "$REAL_DIR" in
    "$INCOMING_DIR"/*) ;;
    *) reject "⚠️ 拒絕：$TASK_DIR 實際指向 $REAL_DIR，跳出了 $INCOMING_DIR，請人工查看" ;;
esac

# ---- 2. 只准出現白名單裡的檔案 ----
# 「多了東西」比「少了東西」嚴重得多：少檔案是傳輸沒完成，多檔案代表有人在放我們
# 沒打算收的內容。尤其 push_agent.sh——舊版就是從這裡拿程式碼的，它現在出現，
# 就是有人正照著舊版的攻擊路徑在試。
for ENTRY in "$TASK_DIR"/* "$TASK_DIR"/.ready; do
    [ -e "$ENTRY" ] || continue
    case "$(basename "$ENTRY")" in
        agent_key|collector_url|.ready) ;;
        push_agent.sh)
            reject "⚠️ 拒絕：$TASK_DIR 裡有 push_agent.sh。agent 程式一律由佈署固定提供，" \
                   "不從投放目錄取得——出現這個檔案代表有人在嘗試讓 root 執行自己的" \
                   "程式碼，請人工查看這台主機" ;;
        *) reject "⚠️ 拒絕：$TASK_DIR 裡有非預期檔案 $(basename "$ENTRY")，請人工查看" ;;
    esac
done

if [ ! -f "$TASK_DIR/.ready" ] || [ -L "$TASK_DIR/.ready" ]; then
    reject "$TASK_DIR 沒有 .ready 標記，可能上傳未完成，這輪跳過"
fi

# ---- 3. 兩個資料檔逐一驗 ----
check_data_file() {
    F="$TASK_DIR/$1"
    [ -e "$F" ] || reject "$TASK_DIR 缺 $1，任務不完整"
    [ -L "$F" ] && reject "⚠️ 拒絕：$1 是符號連結，請人工查看"
    [ -f "$F" ] || reject "⚠️ 拒絕：$1 不是普通檔案，請人工查看"
    SIZE=$(wc -c < "$F" 2>/dev/null || echo 999999)
    [ "$SIZE" -le "$MAX_FILE_BYTES" ] || reject "⚠️ 拒絕：$1 有 $SIZE 位元組，超出上限，請人工查看"
}
check_data_file agent_key
check_data_file collector_url

# agent_key：單行、只允許 base64url 字元。收窄字元集是為了確定它之後被寫進設定檔、
# 被 curl 當標頭送出去時，不會夾帶換行或引號而改變語意。
AGENT_KEY=$(head -n 1 "$TASK_DIR/agent_key")
if ! echo "$AGENT_KEY" | grep -Eq '^[A-Za-z0-9_-]{16,128}$'; then
    reject "⚠️ 拒絕：agent_key 格式不合（只接受 16-128 個 A-Za-z0-9_- 字元），請人工查看"
fi

# collector_url：單行、http(s)、不含空白與 shell 特殊字元。
COLLECTOR_URL=$(head -n 1 "$TASK_DIR/collector_url")
if ! echo "$COLLECTOR_URL" | grep -Eq '^https?://[A-Za-z0-9._-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?$'; then
    reject "⚠️ 拒絕：collector_url 格式不合（$COLLECTOR_URL），請人工查看"
fi

# ---- 4. agent 程式本體必須來自佈署，不是投放目錄 ----
[ -f "$SRC_PUSH_AGENT" ] || reject "找不到 $SRC_PUSH_AGENT（watcher 佈署不完整），無法安裝"
[ -L "$SRC_PUSH_AGENT" ] && reject "⚠️ 拒絕：$SRC_PUSH_AGENT 是符號連結，佈署已被動過，請人工查看"

# ---- 5. 落地 ----
mkdir -p "$CONF_DIR" "$INSTALL_DIR" "$LOG_DIR/done" "$LOG_DIR/failed" "$LOG_DIR/archive"
# push_agent.sh 平常是 sysinfra 身分透過 crontab 執行，直接寫 $LOG_DIR/push_agent.log，
# 這層要開放給它寫；done/failed/archive 是 root 身分的 run_auto_web3.sh 在用，不用動。
chown "$AGENT_USER" "$LOG_DIR"

# key 只給 agent 帳號讀；URL 是非機敏設定，644。
# 這裡寫的是**驗過的變數值**不是複製原檔——原檔就算多了第二行也進不到設定檔。
printf '%s\n' "$AGENT_KEY" > "$CONF_DIR/key"
chown "$AGENT_USER" "$CONF_DIR/key"
chmod 600 "$CONF_DIR/key"
printf '%s\n' "$COLLECTOR_URL" > "$CONF_DIR/collector_url"
chmod 644 "$CONF_DIR/collector_url"

# root:root 0755——agent 帳號自己不能改寫每天以自己身分執行的那支程式，
# 否則 sysinfra 一旦被拿下就能改掉它做到永久駐留。
install -o root -g root -m 755 "$SRC_PUSH_AGENT" "$INSTALL_DIR/push_agent.sh"

# 註冊到 sysinfra 自己的 crontab（不是 root 的）——先濾掉舊的同名項目再加一行，
# 這樣重複跑這支腳本（例如重發 key 後重種一次）不會疊出好幾行一樣的排程。
# 分鐘數用任務名雜湊出 0-59，讓不同主機分散觸發時間，不要整批同一分鐘打 collector。
MINUTE=$(( $(echo "$TASK_DIR" | cksum | cut -d' ' -f1) % 60 ))
CRON_LINE="$MINUTE 3 * * * $INSTALL_DIR/push_agent.sh"
{ crontab -u "$AGENT_USER" -l 2>/dev/null | grep -v "$INSTALL_DIR/push_agent.sh" || true; echo "$CRON_LINE"; } \
    | crontab -u "$AGENT_USER" -

NAME=$(basename "$TASK_DIR")
rm -rf "$TASK_DIR"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) 安裝完成，crontab: $CRON_LINE" >> "$LOG_DIR/done/$NAME.log"
INSTALL_SH_EOF

# ---- push_agent.sh ----
cat > "$BIN_DIR/push_agent.sh" <<'PUSH_AGENT_EOF'
#!/bin/sh
# Push agent 收集本體：每天由 sysinfra 的 crontab 觸發一次，讀本機 disk/memory 狀態，
# 主動 POST 給 collector。不需要 root（讀 df/free/curl 都是一般權限指令）。
#
# 設計原則（2026-08-14 定案）：
# - 這是「狀態回報」不是「即時告警」，一天一次就夠，不用高頻率。
# - 平台辨識不出來（k8s／客製化 Linux）就直接記錄不支援結束，不硬猜著跑。
# - key／collector URL 從安裝時寫好的設定檔讀，不寫死在這支腳本裡——之後要換
#   collector 位址或重發 key，不用重新推這支腳本。

set -eu

CONF_DIR="/etc/webit3-agent"
KEY_FILE="$CONF_DIR/key"
URL_FILE="$CONF_DIR/collector_url"
LOG_FILE="/caslog/webit3_agent/push_agent.log"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >> "$LOG_FILE" 2>/dev/null || true; }

if [ ! -r "$KEY_FILE" ] || [ ! -r "$URL_FILE" ]; then
    log "設定檔缺失（$KEY_FILE / $URL_FILE），無法執行"
    exit 1
fi
KEY=$(cat "$KEY_FILE")
COLLECTOR_URL=$(cat "$URL_FILE")

PLATFORM=$(uname -s)
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# 這輪只支援 Linux 主流發行版（RHEL/CentOS/Debian/Ubuntu/Rocky/Oracle Linux 都用
# 同一套 GNU coreutils df/free，指令一樣）跟 AIX（工具完全不同）。
# k8s／其他客製化 Linux：明確不碰，辨識不出來就記錄結束，不猜著跑。
case "$PLATFORM" in
    Linux)
        if ! command -v df >/dev/null 2>&1 || ! command -v free >/dev/null 2>&1; then
            log "偵測到 Linux 但缺 df/free，可能是客製化/精簡環境，不支援，跳過"
            exit 0
        fi
        DISK_PCT=$(df -P / | awk 'NR==2 { gsub("%","",$5); print $5 }')
        MEM_PCT=$(free | awk '/^Mem:/ { printf "%.1f", ($2-$7)/$2*100 }')
        ;;
    AIX)
        # AIX 沒有 GNU free；記憶體用率抓 svmon -G 的 %comp（近似值，夠日常回報用途）。
        DISK_PCT=$(df -g / | awk 'NR==2 { gsub("%","",$4); print $4 }')
        MEM_PCT=$(svmon -G -O unit=MB 2>/dev/null | awk '/memory/ { printf "%.1f", $3/$2*100; exit }')
        ;;
    *)
        log "不支援的平台：$PLATFORM（k8s/客製化系統不在這輪範圍內），跳過"
        exit 0
        ;;
esac

if [ -z "${DISK_PCT:-}" ] || [ -z "${MEM_PCT:-}" ]; then
    log "資料收集失敗（DISK_PCT/MEM_PCT 是空的），跳過這次回報"
    exit 1
fi

PAYLOAD=$(cat <<EOF
{"metrics":[
  {"key":"disk_used_pct","value":$DISK_PCT,"unit":"%","collected_at":"$NOW"},
  {"key":"memory_busy_pct","value":$MEM_PCT,"unit":"%","collected_at":"$NOW"}
]}
EOF
)

if curl -fsS -m 30 -X POST "$COLLECTOR_URL/api/agent/facts" \
    -H "X-Agent-Key: $KEY" -H "Content-Type: application/json" \
    -d "$PAYLOAD" >/dev/null 2>>"$LOG_FILE"; then
    log "回報成功：disk=$DISK_PCT% mem=$MEM_PCT%"
else
    log "回報失敗（curl 非 0 結束碼），本次跳過，等明天排程重試"
    exit 1
fi
PUSH_AGENT_EOF

chmod 755 "$BIN_DIR/run_auto_web3.sh" "$BIN_DIR/install.sh" "$BIN_DIR/push_agent.sh"
chown root:root "$BIN_DIR/run_auto_web3.sh" "$BIN_DIR/install.sh" "$BIN_DIR/push_agent.sh"

# ---- 註冊 root 的 daily crontab（不是 sysinfra 的——這支要寫系統路徑，需要 root）----
CRON_LINE="7 2 * * * $BIN_DIR/run_auto_web3.sh"
{ crontab -l 2>/dev/null | grep -v "$BIN_DIR/run_auto_web3.sh" || true; echo "$CRON_LINE"; } | crontab -

echo "佈署完成："
echo "  · $BIN_DIR/run_auto_web3.sh 已註冊到 root crontab（$CRON_LINE）"
echo "  · 投放目錄 $INCOMING_DIR（0730 root:$AGENT_USER），只放 agent_key／collector_url"
echo "  · agent 程式本體固定在 $BIN_DIR/push_agent.sh，不從投放目錄取得"
echo "可以安全結束這次 root session、刪掉這支 bootstrap_watcher.sh 了。"
