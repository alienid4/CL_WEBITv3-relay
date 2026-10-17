# Push Agent 安裝流程（2026-08-14 設計，2026-08-28 修提權漏洞後改版）

正式區的正確資產如果要 disk/memory 狀態，這個目錄裡的腳本用兩段式安裝。

## ⚠️ 2026-08-28 改了什麼，以及為什麼一定要改

原設計有一個**真的本機提權漏洞**（不是外觀問題）：

- Stage 1 把 `push_agent.sh`（可執行內容）上傳到 `/tmp/webit3_agent_*`
- Stage 2 的 root 排程去 `/tmp` 撿那個目錄，把裡面的 `push_agent.sh`
  用 root 裝進 `/opt` 755，再排進 sysinfra 的每日 crontab

`/tmp` 是全域可寫，所以**這台機器上任何一個本機帳號**都能自己造一個
`/tmp/webit3_agent_x/`，放進自己寫的 `push_agent.sh` 跟 `.ready`，隔天 root
就會幫他把那份程式裝好、排好班。而 sysinfra 持有全機隊的 SSH 金鑰——
一個本機低權帳號直接變成橫向移動的跳板。不需要任何漏洞利用技巧。

改法就是天條那一句：**root 永遠不執行「來路可控」的程式碼；
外部只能傳資料，不能傳可執行內容。**

| | 舊 | 新 |
|---|---|---|
| 投放目錄 | `/tmp/webit3_agent_*`（全域可寫） | `/var/lib/webit3-agent/incoming`（**0730 root:sysinfra**，其他帳號完全碰不到） |
| `push_agent.sh` 來源 | 上傳上來的那份 | **隨 bootstrap 固定佈署**在 `/opt/webit3-agent/bin/`，安裝時從那裡複製 |
| 投放內容 | key＋URL＋兩支腳本 | **只有 key 與 URL 兩個資料檔**；出現 `push_agent.sh` 或任何非白名單檔案一律拒絕並要求人工查看 |
| 資料驗證 | 只檢查檔案存在 | 逐一驗：非 symlink、普通檔、大小上限、`agent_key` 只收 base64url 16-128 字元、`collector_url` 只收 http(s) 且無空白／shell 特殊字元 |
| 落地方式 | `install` 複製原檔 | 寫的是**驗過的變數值**，原檔多出來的第二行進不到設定檔 |
| 排程掃描前 | 直接掃 | 先檢查投放目錄本身是 `root:730`，權限被放寬就**整輪不做**（前提壞了，個別檢查就沒有意義） |

## Stage 1（sysinfra，一般權限）

呼叫 `POST /api/agent/stage`（body `{"asset_serial": "..."}`）拿到 `agent_key`／
`collector_url` 兩份**資料**，用 sysinfra 帳密 SCP 上傳到目標主機的
`/var/lib/webit3-agent/incoming/<asset_serial>_<timestamp>/`，兩個檔案寫完
**最後才寫一個 `.ready` 標記檔**（避免 Stage 2 撿到上傳到一半的半成品）。

⚠️ **不要上傳 `push_agent.sh` 或 `install.sh`**——那兩支是程式碼，已經隨 bootstrap
固定在主機上了。上傳它們不會生效，反而會讓整個任務被當成攻擊跡象拒絕。

## Stage 2（root，`run_auto_web3.sh`）

已實作在本目錄 `run_auto_web3.sh`。每天掃描一次
`/var/lib/webit3-agent/incoming/*`，看到 `.ready` 的資料夾就執行
`install.sh <task_dir>`。內建：

0. **掃描前先確認投放目錄本身是 `root:730`**，不是就整輪拒絕。這是整輪的前提：
   目錄權限一旦鬆掉，底下每個任務的檢查都失去意義，寧可一個都不做，
   也不要挑幾個看起來沒問題的做。
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
   跑完會把**三支**腳本裝到 `/opt/webit3-agent/bin/`（root:root 0755）、
   建好投放目錄 `/var/lib/webit3-agent/incoming`（0730 root:sysinfra）、
   註冊好 root 的 daily crontab、建好 `/caslog/webit3_agent/` 目錄結構。
   跑完就可以刪掉這支腳本、結束那次 root session——之後再也不需要用 root
   登入這台主機。

⚠️ **`bootstrap_watcher.sh` 是產生出來的，不要直接改它。**
改 `run_auto_web3.sh`／`install.sh`／`push_agent.sh` 任何一支之後跑：

```bash
python backend/agent_scripts/build_bootstrap.py
```

原本這裡寫的是「記得手動同步內嵌區塊」——那是靠人記得，而 8/28 修提權漏洞時
正好證明了風險：只改正本沒改內嵌版的話，**實際佈到主機上的還是有漏洞的舊版，
但 git diff 看起來已經修好了**。「看起來修好、實際沒修」比沒修更危險，
所以改成產生＋測試驗同步（`tests/asset_module/test_agent_scripts_security.py`）。

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
