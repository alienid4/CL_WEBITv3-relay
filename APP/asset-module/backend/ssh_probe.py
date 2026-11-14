"""SSH 測連線（只談判加密、不登入）——放進系統畫面用（2026-09-15）。

## 為什麼要有

舊 Brocade SAN switch（FOS 6.4）只提供 `ssh-dss` 主機金鑰：公司機系統 ssh 連不上、
收集程式用的 paramiko 5.0 也已移除 DSA。paramiko 3.5.1 還支援，但要在真的設備上試過才知道。
使用者 9/15：「把測試小工具也一次放進去，可以放在品質分析裡面」——正式區的 SAN switch
韌體版本資產沒記，網段開通後要逐台試，做成畫面比每次發 tar.gz 實際。

## 兩種都試，結論講白話

1. **新版**：用系統現在的 paramiko（5.x）在後端行程內握手
2. **舊版**：用隔離安裝的 paramiko 3.5.1（`/opt/webit3/legacy_ssh/lib`，patch／部署時安裝）
   開**子行程**跑同一支工具——`PYTHONPATH` 只在那個子行程指過去，系統 venv 完全不受影響

兩者都**只做握手就斷線，不送帳密、不登入、不跑指令**（工具本身有測試斷言認證 0 次）。

## 安全

- 只接受 IP 位址（`ipaddress` 驗證），連主機名都不收——子行程參數不可能被塞東西
- 子行程用參數清單呼叫、**不經 shell**
- 執行的是隨部署固定下來的工具檔（`backend/tools/ssh_legacy_probe.py`），不從外部目錄撿程式
"""
from __future__ import annotations

import importlib.util
import ipaddress
import os
import subprocess
import sys
from pathlib import Path

#: 隔離安裝的舊版 paramiko 位置（patch.sh [4.7/5]／deploy.sh 會裝在這裡）
LEGACY_LIB = Path(os.environ.get("WEBIT_LEGACY_SSH_LIB", "/opt/webit3/legacy_ssh/lib"))
TOOL = Path(__file__).with_name("tools") / "ssh_legacy_probe.py"


def validate(host, port) -> tuple[str, int]:
    """只收 IP 位址與合法埠號。"""
    try:
        ip = ipaddress.ip_address(str(host or "").strip())
    except ValueError:
        raise ValueError("只接受 IP 位址（例：192.0.2.10），不接受主機名或其他字串") from None
    try:
        p = int(port)
    except (TypeError, ValueError):
        raise ValueError("埠號要是數字") from None
    if not 1 <= p <= 65535:
        raise ValueError("埠號要在 1～65535")
    return str(ip), p


def _tool():
    spec = importlib.util.spec_from_file_location("ssh_legacy_probe_tool", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def legacy_available() -> bool:
    return (LEGACY_LIB / "paramiko" / "__init__.py").exists()


def probe_modern(host: str, port: int, timeout: float = 15) -> dict:
    code, lines = _tool().probe(host, port, timeout=timeout)
    return {"code": code, "lines": lines}


def probe_legacy(host: str, port: int, timeout: float = 60) -> dict:
    if not legacy_available():
        return {"available": False, "code": None,
                "lines": [f"舊版 SSH 函式庫還沒安裝（{LEGACY_LIB}）——套最新 patch 或重新部署會自動安裝"]}
    env = dict(os.environ)
    env["PYTHONPATH"] = str(LEGACY_LIB)      # 只有這個子行程看得到 paramiko 3.5.1
    try:
        r = subprocess.run([sys.executable, str(TOOL), host, str(port)],
                           capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"available": True, "code": 124, "lines": [f"✗ 超過 {timeout} 秒沒有結果，已中止"]}
    lines = (r.stdout or "").splitlines()
    err = (r.stderr or "").strip().splitlines()
    if err:
        lines += ["  ── stderr ──"] + [f"  {l}" for l in err[-10:]]
    return {"available": True, "code": r.returncode, "lines": lines}


def verdict(modern: dict, legacy: dict) -> str:
    if modern["code"] == 3:
        return "TCP 連不上：網路或防火牆問題，還沒到 SSH 這一步"
    if modern["code"] == 0:
        return "新版 SSH 就談得成：線上收集可以直接用"
    if legacy.get("code") == 0:
        return "新版談不成、舊版 SSH 談得成：這台需要舊版 SSH（ssh-dss 等弱加密，屬設備韌體限制）"
    if not legacy.get("available"):
        return "新版談不成；舊版 SSH 函式庫還沒安裝，無法判斷舊版能不能用"
    return "新版、舊版 SSH 都談不成：請看談判細節；可能只能用 telnet 或離線匯入"


def run(host, port=22) -> dict:
    host, port = validate(host, port)
    modern = probe_modern(host, port)
    legacy = probe_legacy(host, port)
    return {"ip": host, "port": port, "verdict": verdict(modern, legacy),
            "modern": modern, "legacy": legacy}
