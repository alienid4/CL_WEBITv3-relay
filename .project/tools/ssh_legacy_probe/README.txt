舊版 SSH 測連線小工具
=====================

目的
  測試收集主機能不能用「舊版 SSH（ssh-dss / SHA1 / CBC）」跟舊 SAN switch（Brocade FOS 6.4）談成加密。
  結果決定：做舊版 SSH 收集（仍加密），或退到 telnet（明碼）。

安全
  - 只做 SSH 握手，談成就斷線：不送帳號密碼、不登入、不跑任何指令
  - paramiko 3.5.1 只裝在這個資料夾的 lib/，不碰系統 venv，不需要 root
  - 所有腳本都是明碼，可先打開看內容

使用（在收集主機上，一般帳號即可）
  tar xzf ssh_legacy_probe_*.tar.gz
  cd ssh_legacy_probe
  bash run.sh <switch 管理IP>　（例：bash run.sh 10.99.18.10）

看結果
  ✓ SSH 談判成功  → 舊版 SSH 可行，把整段輸出貼給開發者
  ✗ SSH 談判失敗  → 把整段輸出（含「談判細節」）貼給開發者
  ✗ TCP 連不上    → 網路／防火牆問題，還沒到 SSH

結束碼：0 成功／2 SSH 談判失敗／3 TCP 連不上／4 參數錯誤
