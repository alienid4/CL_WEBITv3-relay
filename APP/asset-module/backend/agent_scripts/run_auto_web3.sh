#!/bin/sh
# root 排程 watcher 本體。由 root 的 crontab 每天觸發一次（見 bootstrap_watcher.sh
# 註冊的排程），掃描 /tmp/webit3_agent_* 底下已上傳完整（有 .ready 標記）的任務，
# 通過資源檢查閘門＋lockfile 防重疊之後，逐一執行 install.sh 落地。
#
# 設計原則（2026-08-14 定案，詳見 backend/agent_scripts/README.md）：
# - 不是即時系統，一天掃一次就夠，安裝 agent 這件事完全不急。
# - 資源檢查沒過就跳過這輪，留給明天重試，不強跑；連續跳過太多天要留痕跡讓人知道。
# - lockfile 用 PID 存活判斷是否重疊執行，不是用時間猜測——正確處理「上次意外中斷、
#   沒清乾淨鎖檔」的情況。

set -eu

BASE_DIR="/opt/webit3-agent"
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

for TASK_DIR in /tmp/webit3_agent_*; do
    [ -d "$TASK_DIR" ] || continue
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
