# 給 Codex：程式碼健檢（只看、只提建議，不改程式）

> 這份檔案會跟著 relay 進到公司 Workstation。請照下面的範圍與格式產出一份報告，
> 交給使用者，由使用者帶回家裡的開發機處理。**這次不要改任何程式、不要 commit。**

## 1. 專案是什麼（30 秒版）

- 「資訊戰情室」資產盤點系統：FastAPI（Python 3.11）＋ Nuxt 3（Vue）＋ SQLite。
- 主要功能：匯入資產清冊（CIA／RVTools／Excel）→ 網段掃描 → 納管（在 Linux／AIX 建唯讀收集帳號、
  Windows 走 WinRM/PSRP）→ 收集硬體／軟體／服務／帳號／排程／組態檢核／OS 重建資料
  → 台數統計、報表、資料校正（實測值校正清冊）。
- 規模：backend 約 130 支 .py（約 5.8 萬行）、frontend 約 46 頁（約 3.3 萬行）、tests 約 260 支。
- 程式碼位置：`APP/asset-module/backend/`、`APP/asset-module/frontend/`、`tests/asset_module/`。

## 2. 請重點看這幾區（依重要性）

| # | 區域 | 主要檔案 | 想知道的事 |
|---|---|---|---|
| 1 | 納管引擎（會在目標主機建帳號、改 sudoers） | `onboard_engine.py`、`onboard_bundle.py`、`post_onboard.py`、`root_key.py` | 有沒有可能在不該納管的機器上產生副作用？失敗時會不會留下半套狀態？ |
| 2 | 收集分派與排隊 | `collect_dispatch.py`、`post_onboard.py`（記憶體佇列）、`routers/collect_scan.py` | 執行緒與 SQLite 連線的用法安全嗎？取消、逾時、服務重啟時的狀態會不會錯？ |
| 3 | Windows 收集（PSRP） | `winrm*`、`win_rebuild_collector.py`、`schedule_inventory.py`、`config_audit.py` 的 Windows 段 | 送到 Windows 的 PowerShell 有沒有會觸發 SOC 的寫法（見 §4）？權限不足時有沒有被當成「0 筆」？ |
| 4 | 台數與「同一台」判斷 | `manage_state.py`、`system_stats`（machine_key）、`lifecycle.py`、`corrections`／`collected_os.py`、`verify_numbers.py` | 同一個規則有沒有在多處各寫一份？加總與下鑽會不會不一致？ |
| 5 | 超大檔案 | `frontend/pages/adopt.vue`（約 3100 行）、`pages/assets/[serial].vue`（約 3600 行）、`onboard_engine.py`（約 2600 行）、`collect_dispatch.py`（約 2200 行） | 該怎麼拆？哪些邏輯在前端與後端重複？ |
| 6 | 錯誤處理 | 全域 | 過寬的 `except Exception`、把錯誤吞掉變成「沒有資料」、前端 `catch` 後沒顯示 |
| 7 | 安全 | `routers/*`（require_auth）、`credential_store.py`、遮蔽祕密的函式 | 有沒有端點漏掛驗證？輸入（IP、主機名、路徑）有沒有沒驗就拼進指令？祕密有沒有可能進 log／DB 原文？ |
| 8 | 效能 | 各 router 的查詢 | N+1 查詢、每次開頁都重算的重計算、缺索引 |
| 9 | 測試品質 | `tests/asset_module/` | 測試是否走真實路徑（經 API）而不是只測函式？有沒有永遠會過的假測試？ |

## 3. 已知、不用再報的事

- 「每台主機一把尺」：同一台判斷只能用 `machine_key`，已有測試釘住；除非發現新的繞道寫法。
- `WEBIT3_DISABLE_AUTH`（登入停用）是開發機刻意開的，正式環境會關。
- OS 重建、組態檢核的「自製判定 V001／W-V001」不是官方 FCB，已標示。

## 4. 專案的硬規則（建議不能違反這些）

- 送到 Windows 的指令：**不准** `-ExecutionPolicy`／Bypass、不准寫 .ps1 檔再執行、不准 `-EncodedCommand`／base64、
  不准 `Invoke-Expression`、`Add-Type`、不准啟動 net.exe／sc.exe／reg.exe／appcmd 等外部程式（會觸發 SOC 告警）。
- Linux sudoers 規則**不准萬用字元**；不准 sshpass；SSH 主機金鑰用 accept-new。
- VMware（ESXi、vCenter）**一律不可納管、不可建帳號**。
- 資產狀態（使用中／停用／報廢／閒置）**只能由 CIA 匯入寫入**，程式不得自行改。
- 例子 IP 一律用 `10.99.x`，報告裡也不要寫真實 IP 或公司名稱。

## 5. 可以做、不可以做

- ✅ 讀程式、跑測試：`python -m pytest tests -q -x`（若有 xdist 可加 `-n auto`）。
- ✅ 在本機起服務看畫面（不連任何真實主機）。
- ❌ 不改程式、不 commit、不 push。
- ❌ 不對任何真實主機做掃描、納管、收集。

## 6. 交件格式（請照這個寫，一份 Markdown）

檔名：`CODEX_REVIEW_RESULT_<日期>.md`

```
## 摘要
- 最嚴重的 3 件事（一行一件）
- 整體評分：架構 / 安全 / 可維護性 / 測試，各 1–5 分，各一句理由

## 發現清單
| # | 嚴重度 | 區域(§2 編號) | 檔案:行號 | 問題（一句話） | 會造成什麼後果 | 建議怎麼改 | 估計工作量 |
（嚴重度：高＝可能造成資料錯誤／資安問題／對主機產生副作用；中＝可能出錯或難維護；低＝風格／小改善）

## 拆檔建議
- 針對 §2 第 5 項，每個大檔建議拆成哪幾個模組、各放什麼

## 我沒看懂或沒看完的地方
- 列出來，不要猜
```

規則：
1. 每一條都要有 **檔案:行號** 當證據；沒有證據的推論請標「推論」。
2. 嚴重度「高」的請附一段最小重現方式或測試想法。
3. 不要列「可以更好」這種沒有具體改法的建議。
4. 依嚴重度排序，同嚴重度依 §2 編號排序。
