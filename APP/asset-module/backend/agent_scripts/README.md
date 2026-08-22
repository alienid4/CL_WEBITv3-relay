# Push Agent 安裝流程（2026-08-14 設計）

正式區的正確資產如果要 disk/memory 狀態，這個目錄裡的兩支腳本用兩段式安裝：

## Stage 1（sysinfra，一般權限）

呼叫 `POST /api/agent/stage`（body `{"asset_serial": "..."}`）拿到 `agent_key`／
`collector_url`／`push_agent.sh`／`install.sh` 四份內容，用 sysinfra 帳密 SCP 上傳到
目標主機的 `/tmp/webit3_agent_<asset_serial>_<timestamp>/`，全部檔案寫完**最後才寫
一個 `.ready` 標記檔**（避免 Stage 2 撿到上傳到一半的半成品）。

## Stage 2（root，`run_auto_web3.sh`）

已實作在本目錄 `run_auto_web3.sh`。每天掃描一次 `/tmp/webit3_agent_*`，看到 `.ready`
的資料夾就執行 `install.sh <task_dir>`。內建：

1. **lockfile（存自己 PID）防重疊執行**：lockfile 已存在 → 檢查裡面的 PID 是否還存活
   （`kill -0`），存活就跳過，不存活（代表上次意外中斷沒清乾淨）就安全地蓋掉繼續執行。
2. **資源檢查閘門**（沒過就跳過這輪，留給明天重試，不強跑）：CPU 1分鐘負載平均/核心數
   < 0.8、可用記憶體 > 5%（裝 agent 本身很輕量，門檻比日常收集寬鬆）、`/` 與 `/caslog`
   使用率 < 85%；連續跳過達 3 天就寫一筆警示日誌，不自動硬跑。
3. 資源檢查過了才執行 `install.sh <task_dir>`。
4. housekeeping：`/caslog/webit3_agent/{done,failed}` 超過 30 天的壓縮進 `archive/`。

## 首次佈署（唯一需要 root/PAM 的一次性動作）

`run_auto_web3.sh` 要能存在於主機上，第一次一定要有人用 root 權限把它跟
`install.sh` 放到系統路徑、註冊 root crontab——這一步繞不過去，但只需要做**一次**：

1. 透過既有的 PAM 代登流程或變更管理程序，把 `bootstrap_watcher.sh`（本目錄，
   已內嵌 `run_auto_web3.sh`／`install.sh` 全部內容，單一檔案就完整，不用另外帶）
   傳到目標主機任意路徑，例如 `/tmp/bootstrap_watcher.sh`。
2. root 登入後執行**一行指令**：
   ```bash
   sudo bash /tmp/bootstrap_watcher.sh
   ```
   跑完會把兩支腳本裝到 `/opt/webit3-agent/bin/`、註冊好 root 的 daily crontab、
   建好 `/caslog/webit3_agent/` 目錄結構。跑完就可以刪掉這支腳本、結束那次 root
   session——之後再也不需要用 root 登入這台主機。

⚠️ **`bootstrap_watcher.sh` 是手動同步的內嵌檔**：改了 `run_auto_web3.sh` 或
`install.sh` 的內容，記得同步更新 `bootstrap_watcher.sh` 裡對應的 heredoc 區塊
（`RUN_AUTO_WEB3_EOF`／`INSTALL_SH_EOF`），不是自動同步。

## 日常執行

`push_agent.sh` 裝好之後由 **sysinfra 自己的 crontab**（不是 root）每天觸發一次，
讀本機 disk/memory、POST 給 collector——這個動作完全不需要特權。

## 這輪範圍

- 只收 disk 使用率／memory busy 兩項
- OS：Linux 主流發行版（RHEL/CentOS/Debian/Ubuntu/Rocky/Oracle Linux）＋ AIX
- 明確不碰：k8s、其他客製化/非主流 Linux
- 之後要加項目（netstat/IP/VG LV/CPU/swap/GBIC/軟體盤點/硬體盤點/帳號盤點/連通拓譜圖/
  服務盤點），照同一套機制擴充：server 端下發要收集什麼清單，agent 端已備好收集函式，
  開關由 server 端控制，不用重新推每台主機的腳本。
