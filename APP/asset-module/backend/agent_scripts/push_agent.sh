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
