#!/bin/sh
# Push agent「落地」腳本，由 root 排程 watcher（`run_auto_web3.sh`，需另外佈署，
# 見 backend/agent_scripts/README.md）在通過資源檢查閘門＋lockfile 防重疊之後執行。
#
# 用法：install.sh <task_dir>
#   task_dir 是 sysinfra 用一般權限上傳到 /tmp 的那個任務資料夾，內含：
#     agent_key       — 明文 key，落地後改存 /etc/webit3-agent/key（600）
#     collector_url   — collector 的 URL
#     push_agent.sh   — 日常執行的收集腳本本體
#     .ready          — 標記檔，代表上傳完整（呼叫端已確認過才會叫這支）
#
# 這支腳本只做「寫檔案＋設權限＋註冊排程」，需要 root 是因為要寫 /etc、/opt 系統路徑；
# 但日常真正執行 push_agent.sh 的排程是註冊在 sysinfra 的 crontab，不是 root 的——
# 裝的當下才用到 root，之後每天真正在跑的動作權限最小化。

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
# push_agent.sh 平常是 sysinfra 身分透過 crontab 執行，直接寫 $LOG_DIR/push_agent.log，
# 這層要開放給它寫；done/failed/archive 是 root 身分的 run_auto_web3.sh 在用，不用動。
chown "$AGENT_USER" "$LOG_DIR"

install -o "$AGENT_USER" -m 600 "$TASK_DIR/agent_key" "$CONF_DIR/key"
install -m 644 "$TASK_DIR/collector_url" "$CONF_DIR/collector_url"
install -m 755 "$TASK_DIR/push_agent.sh" "$INSTALL_DIR/push_agent.sh"

# 註冊到 sysinfra 自己的 crontab（不是 root 的）——先濾掉舊的同名項目再加一行，
# 這樣重複跑這支腳本（例如重發 key 後重新種一次）不會疊出好幾行一樣的排程。
# 分鐘數用資產序號雜湊出一個 0-59 的值，讓不同主機分散觸發時間，不要整批同一分鐘打 collector。
MINUTE=$(( $(echo "$TASK_DIR" | cksum | cut -d' ' -f1) % 60 ))
CRON_LINE="$MINUTE 3 * * * $INSTALL_DIR/push_agent.sh"
{ crontab -u "$AGENT_USER" -l 2>/dev/null | grep -v "$INSTALL_DIR/push_agent.sh" || true; echo "$CRON_LINE"; } \
    | crontab -u "$AGENT_USER" -

rm -rf "$TASK_DIR"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) 安裝完成，crontab: $CRON_LINE" >> "$LOG_DIR/done/$(basename "$TASK_DIR").log"
