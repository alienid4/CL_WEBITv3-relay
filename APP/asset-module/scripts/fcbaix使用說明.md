# AIX 組態檢核 `fcbaixsh` 使用說明

對象：公司 8 台 AIX 7.2。Claude 連不到公司設備，**由管理者自己執行**，
把產出檔回傳給 Claude 判讀。

---

## 一、傳檔上去

用 WinSCP／scp 把 `fcbaixsh` 放到 AIX 主機，例如 `/home/<你的帳號>/fcbaixsh`。

**從 Windows 傳過去一定會帶 CRLF 換行**，ksh 會噴 `^M: not found`。上去後先轉一次：

```
tr -d '\r' < fcbaixsh > fcbaixsh.tmp && mv fcbaixsh.tmp fcbaixsh
```

確認沒有 `^M`（應該沒有輸出）：

```
grep -c $'\r' fcbaixsh
```

---

## 二、執行

需要 root（非 root 會有 30 條以上 Error，那份結果不能用）。

```
ksh ./fcbaixsh
```

不要用 `sh ./fcbaixsh`——AIX 的 `/usr/bin/sh` 是 bsh，語法不同。

輸出目錄預設 `/var/tmp/fcbaudit`（腳本會自己建，權限 0750）。
要換地方：

```
FCBAIX_OUTDIR=/some/other/dir ksh ./fcbaixsh
```

整支約數秒跑完，唯讀、不改任何系統設定。

---

## 三、要回傳給 Claude 的東西

`/var/tmp/fcbaudit/` 底下三個檔，**全部都要**：

| 檔案 | 用途 |
|---|---|
| `FCB_<主機名>_<IP>.txt` | 檢核結果本體（就是要匯進系統的那份） |
| `FCB_<主機名>_<IP>.debug.log` | 每一條實際判定與原始輸出，出問題靠這個查 |
| `FCB_<主機名>_<IP>.html` | 給人看的彩色版 |

若腳本整支跑不起來（例如一開頭就 syntax error），
**把畫面從第一行截圖**回傳——開頭的環境自檢區會列出
`uname -s`／`oslevel -s`／執行身分／13 個指令在不在／8 個關鍵檔可不可讀，
那段就足以判斷原因。

---

## 四、怎麼看結果對不對

最後一行一定是：

```
Check summary: 合計 98 項；Compliant xx；Non-Compliant xx；Not-Applicable xx；Error xx
```

**沒有這一行 = 腳本中途死掉，那份檔案不算數**，不要拿去匯入。

四種結果的意思：

| 結果 | 意思 |
|---|---|
| `Compliant` | 合規 |
| `Non-Compliant` | 不合規，要處理 |
| `Not-Applicable` | 這台沒用到（例如沒有 NFS），不列入分母 |
| `Error` | **沒查到**——指令不存在、檔案讀不到、權限不足。不是合規也不是不合規 |

`Error` 多就代表這份報告不可信。特別是非 root 執行會一次冒出 30 條以上。

合規率的分母是 **總數 − Not-Applicable − Error**，不是 98。

---

## 五、第一輪預期會踩到的雷（Claude 沒有 AIX 可先驗，這是憑經驗列的）

以下都是**推論不是證據**，請實際跑完回報：

1. `lssec` 的節名：`/etc/security/user` 的 `default` 節。若貴司用其他節（例如 `sysadmin`），
   86/91~96 會判錯，要調整
2. SNMP 設定檔路徑：腳本先找 `/etc/snmpdv3.conf`，找不到再找 `/etc/snmpd.conf`。
   若實際用其他檔名，64~66 會變 Not-Applicable
3. `/etc/exports` 的寫法各家不同，19~22 可能誤判
4. `/var/tmp/dpid2.log` 等三個 SNMP log（80~82）若未啟用 SNMP 會是 Not-Applicable
5. 檔案權限項（71~82）的期望值是照檢核表原文寫死的，
   若貴司標準與檢核表不同會誤報

回報時把 `debug.log` 一起給，Claude 才改得準。

---

## 六、這 98 條的基準是 CIS，不是 TWGCB（稽核一定會問）

**AIX 對應的是 CIS，不是 TWGCB。** 證據在檢核表自己身上：

| 檢核表裡的東西 | 對應什麼 |
|---|---|
| 類別欄 `Secure Configuration of Enterprise Assets and Software`（86 條）、`Account Management`（12 條） | **CIS Controls v8** 的控制項 4 與 5 |
| 原則設定名稱字尾 `(Automated)` 96 條／`(Manual)` 2 條 | **CIS Benchmark** 的標準標記法 |
| 上游文件 | `CIS IBM AIX 7.2 Benchmark`（現行 v1.1.0） |

輸出檔表頭已寫明「檢核基準: CIS IBM AIX 7.2 Benchmark」，
每一條的「標準設定值」欄末也標了 `[CIS Controls v8 #4 …]` 或 `[#5 帳號管理]`。

### 條號怎麼來的（2026-09-22 改版）

基準是 **CIS IBM AIX 7 Benchmark v1.2.0**（CIS 官方 PDF，567 頁，使用者自行下載）。

> **原本用的是 v1.0.0，2026-09-22 全部改成 v1.2.0。**
> 使用者拍板「以 1.2 為主」「可以改，我們只要註明參考來源」。

| | 條數 |
|---|---|
| 對到 v1.2.0 真條號 | **97** |
| v1.2.0 查無對應（本行自訂 `A-01`） | **1**（`cas_agent`） |

**為什麼非改不可**：v1.2.0 章節整個重編，98 條裡 **87 條換號、0 條沒變**。
只補缺的、其餘沿用舊號的話，報表上 87 條都是錯的引用——
**混用兩個版本比缺號更糟，稽核一比就發現對不上**。

對照方法：用**指令名／參數名／檔案路徑**當錨點（`writesrv`／`minlen`／`/etc/passwd`，
跨版本不會變），不用標題字串比——v1.2.0 標題改成 `Ensure ...` 句型
（v1.0.0 是 `Disable writesrv`、`pop3`），直接比會大量落空。

逐條變更紀錄（含舊號、新號、v1.2.0 標題）在 `AI/CIS條號對照_v1.0.0_to_v1.2.0.md`。
**舊號只留在那份紀錄裡，不上畫面**——兩個版本的號同時出現，看的人不知道該引用哪個。

## 六之二、跟 TWGCB 的關係

**TWGCB 沒有發布 AIX 版。** 已發布的是 Windows 10/11、Windows Server、
RHEL 8/9、Ubuntu、macOS 等；AIX 不在清單內。所以：

- 本檔 ID 一律是本行自訂的 `FCB-AIX-0001~0098`，
  **不會也不可以自己編一個 TWGCB 號碼**——那是造假，稽核一問就破
- 能做的是**語意對應**：AIX 這一條等同 RHEL TWGCB 的哪一條。
  對得上的在「標準設定值」欄末加註，共 **22 條**：
  - `(對應 TWGCB-xxx)` = 語意確實相同（例如 PermitEmptyPasswords、MaxAuthTries、
    IgnoreRhosts、唯一的GID、密碼最小長度、密碼最短/最長使用期限、登入失敗鎖定次數）
  - `(近似 TWGCB-xxx)` = 只是接近，不可直接畫等號（例如 AIX `minother` 把
    數字與特殊字元合併成一個值，RHEL 是 0210／0213 兩條分開算）
- 其餘約 76 條（AIX 專屬的 `inittab`／`rc.tcpip`／`inetd.conf`／`no` 網路參數等）
  **TWGCB 沒有對應項**，就不加註，不硬湊

跨平台要合併統計時，**用「類別」對齊，不要用 ID 對齊**——
AIX 這 98 條已經收進跟 RHEL 完全相同的 5 個類別：
系統服務／系統設定與維護／SSH設定／日誌與稽核／帳號與存取控制。
