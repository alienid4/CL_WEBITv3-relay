#!/usr/bin/env bash
# 舊版 SSH 測連線小工具（只談判加密、不登入、不跑指令、不改系統）
#
# 用法：  bash run.sh <IP> [port]
# 例：    bash run.sh 192.0.2.223
#
# 會做的事（純文字，請先看過）：
#   1. 把附帶的 paramiko 3.5.1 裝進「這個資料夾裡的 lib/」——不碰系統 venv、不需要 root
#   2. 用 PYTHONPATH 只讓這一次執行看到 lib/ 裡的 paramiko，然後跑 ssh_legacy_probe.py
# 相依的 cryptography／bcrypt／pynacl 沿用現有 venv 的（版本相容，已驗證）。
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PY="${WEBIT_PY:-/opt/webit3/venv/bin/python}"
LIB="$DIR/lib"

if [ "$#" -lt 1 ]; then
  echo "用法：bash run.sh <IP> [port]"
  exit 4
fi
if [ ! -x "$PY" ]; then
  echo "找不到 Python：$PY（可用 WEBIT_PY=/path/to/python bash run.sh <IP> 指定）"
  exit 4
fi

if [ ! -d "$LIB/paramiko" ]; then
  umask 022
  "$PY" -m pip install --no-index --no-deps --target "$LIB" "$DIR"/paramiko-3.5.1-py3-none-any.whl >/dev/null
fi

PYTHONPATH="$LIB" "$PY" "$DIR/ssh_legacy_probe.py" "$@"
