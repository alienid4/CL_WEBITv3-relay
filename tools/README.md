# Tools

不用套整包 patch 就能單獨下載的小工具。點檔名 → 右上角 **Raw** → 另存新檔，
用 WinSCP 傳到主機的 `/tmp/` 執行即可。

> 這裡的版本與 patch 內的 `APP/asset-module/` 同步。已經套過最新 patch 的機器，
> 這兩支就在 `/opt/webit3/app/` 底下，不必再下載。

## diagnose.sh — 一鍵健檢

出狀況時跑這支，把**整份輸出**貼回給 AI，不必再一條一條複製指令。

```bash
sudo bash diagnose.sh
```

會檢查：版本與服務、實際監聽的埠、資料概況（各表筆數、來源分布）、重複登記、
待人工審核的原因分布、掃描設定與尚未設定的網段、近 24 小時錯誤、磁碟與備份。

**唯讀**：只查詢不修改，不重啟服務、不動資料，正式機上隨時可跑。
報告同時存一份到 `/opt/webit3/data/logs/diagnose_<時間>.txt`。

輸出預設以統計數字為主。主機名與 IP 屬內網資訊，貼進對話前能少帶就少帶；
真的需要看明細時才加 `--detail`。

## check_env.sh — 安裝前環境探測

**還沒安裝**的機器上先跑這支，確認環境合不合格，避免 `setup.sh` 跑到一半才卡住。

```bash
bash check_env.sh
```

會檢查：OS 版本、SELinux 模式、python3.11／node20／git、外網連通（PyPI／npm／GitHub）、
proxy、連接埠占用、磁碟空間、服務帳號。不需要 root。

## 換行符

這個 repo 有 `.gitattributes` 強制 `*.sh` 用 LF，所以正常下載不會壞。
萬一在主機上出現 `$'\r': command not found`，代表中途被某個工具轉成 CRLF，修法：

```bash
sed -i 's/\r$//' diagnose.sh
```
