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
