#!/bin/sh
# ============================================================================
# Push agent watcher 一次性佈署腳本——root 登入後只要這一行：
#
#     sudo bash bootstrap_watcher.sh
#
# 這是唯一需要 root/PAM 代登的步驟，執行完可以立刻結束那次 root session、
# 刪掉這支腳本。之後 watcher 自己每天用 root crontab 跑，日常 agent 資料回報
# 走 sysinfra 的 crontab，兩邊都不需要人再登入。
#
# ⚠️ 這支腳本是自帶內容（self-contained）：把 run_auto_web3.sh／install.sh 的
# 內容內嵌在下面，只要傳這一份檔案上去就完整，不用另外帶兩份。
# 來源檔在 backend/agent_scripts/run_auto_web3.sh 與 install.sh——那兩份改了，
# 這支要重新產生（不是自動同步，改一邊記得也改這裡，見 README.md）。
# ============================================================================

set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "bootstrap_watcher.sh 需要 root 權限執行（sudo bash bootstrap_watcher.sh）" >&2
    exit 1
fi

BIN_DIR="/opt/webit3-agent/bin"
LOG_DIR="/caslog/webit3_agent"

mkdir -p "$BIN_DIR" "$LOG_DIR/done" "$LOG_DIR/failed" "$LOG_DIR/archive"

# ---- run_auto_web3.sh ----
cat > "$BIN_DIR/run_auto_web3.sh" <<'RUN_AUTO_WEB3_EOF'
#!/bin/sh
# root 排程 watcher 本體。由 root 的 crontab 每天觸發一次（見本腳本結尾的
# crontab 註冊），掃描 /tmp/webit3_agent_* 底下已上傳完整（有 .ready 標記）
# 的任務，通過資源檢查閘門＋lockfile 防重疊之後，逐一執行 install.sh 落地。

set -eu

BASE_DIR="/opt/webit3-agent"
LOG_DIR="/caslog/webit3_agent"
LOCK_FILE="$LOG_DIR/.run_auto_web3.lock"
SKIP_COUNT_FILE="$LOG_DIR/.skip_count"
INSTALL_SH="$BASE_DIR/bin/install.sh"

MAX_LOAD_RATIO="0.8"
MIN_MEM_FREE_PCT="5"
MAX_DISK_USED_PCT="85"
SKIP_ALERT_THRESHOLD="3"
RETENTION_DAYS="30"

mkdir -p "$LOG_DIR/done" "$LOG_DIR/failed" "$LOG_DIR/archive"
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >> "$LOG_DIR/run_auto_web3.log"; }

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

find "$LOG_DIR/done" "$LOG_DIR/failed" -maxdepth 1 -type f -mtime "+$RETENTION_DAYS" 2>/dev/null | \
    while read -r OLD_LOG; do
        gzip -c "$OLD_LOG" > "$LOG_DIR/archive/$(basename "$OLD_LOG").gz" 2>/dev/null && rm -f "$OLD_LOG"
    done
RUN_AUTO_WEB3_EOF

# ---- install.sh ----
cat > "$BIN_DIR/install.sh" <<'INSTALL_SH_EOF'
#!/bin/sh
# Push agent「落地」腳本，用法：install.sh <task_dir>

set -eu

TASK_DIR="${1:?用法：install.sh <task_dir>}"
AGENT_USER="sysinfra"
CONF_DIR="/etc/webit3-agent"
INSTALL_DIR="/opt/webit3-agent"
LOG_DIR="/caslog/webit3_agent"

if [ "$(id -u)" -ne 0 ]; then
    echo "install.sh 需要 root 權限" >&2
    exit 1
fi

if [ ! -f "$TASK_DIR/.ready" ]; then
    echo "$TASK_DIR 沒有 .ready 標記，可能上傳未完成，拒絕執行" >&2
    exit 1
fi
for f in agent_key collector_url push_agent.sh; do
    if [ ! -f "$TASK_DIR/$f" ]; then
        echo "$TASK_DIR 缺 $f，任務不完整" >&2
        exit 1
    fi
done

mkdir -p "$CONF_DIR" "$INSTALL_DIR" "$LOG_DIR/done" "$LOG_DIR/failed" "$LOG_DIR/archive"
chown "$AGENT_USER" "$LOG_DIR"

install -o "$AGENT_USER" -m 600 "$TASK_DIR/agent_key" "$CONF_DIR/key"
install -m 644 "$TASK_DIR/collector_url" "$CONF_DIR/collector_url"
install -m 755 "$TASK_DIR/push_agent.sh" "$INSTALL_DIR/push_agent.sh"

MINUTE=$(( $(echo "$TASK_DIR" | cksum | cut -d' ' -f1) % 60 ))
CRON_LINE="$MINUTE 3 * * * $INSTALL_DIR/push_agent.sh"
{ crontab -u "$AGENT_USER" -l 2>/dev/null | grep -v "$INSTALL_DIR/push_agent.sh" || true; echo "$CRON_LINE"; } \
    | crontab -u "$AGENT_USER" -

rm -rf "$TASK_DIR"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) 安裝完成，crontab: $CRON_LINE" >> "$LOG_DIR/done/$(basename "$TASK_DIR").log"
INSTALL_SH_EOF

chmod 755 "$BIN_DIR/run_auto_web3.sh" "$BIN_DIR/install.sh"

# ---- 註冊 root 的 daily crontab（不是 sysinfra 的——這支要寫系統路徑，需要 root）----
CRON_LINE="7 2 * * * $BIN_DIR/run_auto_web3.sh"
{ crontab -l 2>/dev/null | grep -v "$BIN_DIR/run_auto_web3.sh" || true; echo "$CRON_LINE"; } | crontab -

echo "佈署完成：$BIN_DIR/run_auto_web3.sh 已註冊到 root crontab（$CRON_LINE）。"
echo "可以安全結束這次 root session、刪掉這支 bootstrap_watcher.sh 了。"
