"""收集用 SSH 的共用連線層：同一台只開一條連線、不並行、登入前被斷線要重試並講實話。

## 為什麼要有（2026-10-21 公司 v1.480.2 實測）

RHEL 9.6 一台「收集全部」：硬體規格 ✓ 5.6 秒，接著服務／軟體／帳號／OS 重建／組態檢核
全部在 0.1～1.1 秒內 ✕。OS 重建的訊息裡有原文：
`kex_exchange_identification: read: Connection reset by peer … port 22`。

原因：每一條收集指令都是一支獨立的 `ssh` 行程＝一條新的 TCP 連線。收集全部七樣、
每樣幾條到十幾條指令，幾秒內對同一台開幾十條連線，目標機的 sshd（MaxStartups）或
防火牆／IPS（短時間多次連線限制）在**登入前**就把連線砍掉。

## 做法

1. **連線共用**：OpenSSH ControlMaster（`ControlMaster=auto`＋`ControlPersist`）——
   同一台、同一個帳號的所有指令（不管是哪一個收集器、哪一樣）走同一條連線。
   每條指令仍是一次 `subprocess.run`（離開碼／stdout／stderr 照舊各自分開）。
2. **同一台不並行**：全行程一把「每台一把」的鎖，納管後收集、收集全部、批次、排程
   同時碰到同一台，也只會一條一條來。
3. **登入前被斷線**（kex_exchange_identification／Connection reset／banner exchange）：
   等 2、5、10 秒重試；仍失敗就講實話（見 PREAUTH_MSG），並在 COOLDOWN_S 秒內
   對這台的後續指令直接回同一個原因——不要再對一台正在擋我們的機器連打幾十次。

關閉：環境變數 `WEBIT3_SSH_MUX=0`（只關連線共用；鎖與重試照舊）。Windows 上的 ssh
不支援 ControlMaster，自動不用。
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time

#: 登入前被斷線的特徵（ssh 自己印的 stderr）。只認 sshd/防火牆在**驗證前**就切斷的樣式，
#: Permission denied（帳號／金鑰）、timed out（機器不在）不在此列——那些要做的事不同。
PREAUTH_PATTERNS = (
    "kex_exchange_identification",
    "ssh_exchange_identification",
    "banner exchange",
)
#: 「Connection reset by <ip> port 22」＝ssh 在交換版本字串時被砍（登入前的寫法）。
#: 不認單獨的「Connection reset by peer」：登入後斷線（client_loop: send disconnect…）也長那樣，
#: 那時指令可能已經跑過了，不可以重送（獨立審查 2026-10-21）。
_PREAUTH_RESET_RE = re.compile(r"connection reset by \S+ port \d+")

PREAUTH_MSG = ("目標主機 SSH 在登入前就斷線（可能是連線數限制 MaxStartups 或"
               "防火牆／IPS 擋短時間多次連線）")

#: 重試前各等幾秒（共 3 次重試）
BACKOFF_S = (2, 5, 10)
#: 最後仍失敗後，這段時間內對同一台的指令直接回同一個原因，不再連
COOLDOWN_S = 30
#: 連線共用：最後一條指令跑完後，共用連線再留幾秒（同一輪收集的下一樣接得上）。
#: 不留太久：批次收上千台時，每台留著的 ssh 行程會堆起來。
PERSIST_S = 15

_sleep = time.sleep          # 測試換掉，不真的等
_clock = time.monotonic

_locks_guard = threading.Lock()
_host_locks: dict[str, threading.Lock] = {}
#: {host: (monotonic 時間, 訊息)}：最近一次「重試完仍在登入前被斷線」
_preauth_fail: dict[str, tuple[float, str]] = {}
#: {host: monotonic 時間}：最近一次把這個原因回給呼叫端（含冷卻期間直接回的）。
#: 呼叫端（post_onboard）用它判「這一樣跑的期間是不是撞到登入前斷線」。
_preauth_seen: dict[str, float] = {}
_mux_dir_ok: list = [None]


def host_lock(host: str) -> threading.Lock:
    with _locks_guard:
        lk = _host_locks.get(host)
        if lk is None:
            lk = _host_locks[host] = threading.Lock()
        return lk


def mux_dir() -> str:
    d = os.environ.get("WEBIT3_SSH_MUX_DIR")
    if d:
        return d
    from onboard_engine import SSH_KNOWN_HOSTS

    return os.path.join(os.path.dirname(SSH_KNOWN_HOSTS) or ".", "ssh_mux")


def mux_enabled() -> bool:
    if os.environ.get("WEBIT3_SSH_MUX", "1") == "0" or os.name == "nt":
        return False
    if _mux_dir_ok[0] is None:
        try:
            d = mux_dir()
            os.makedirs(d, mode=0o700, exist_ok=True)
            os.chmod(d, 0o700)        # ssh 拒用別人寫得進去的 ControlPath 目錄
            _mux_dir_ok[0] = True
        except OSError:
            _mux_dir_ok[0] = False    # 建不了目錄＝不共用（每條指令照舊各自連），不擋收集
    return bool(_mux_dir_ok[0])


def mux_opts() -> list[str]:
    """加在 ssh 參數裡的連線共用選項；不能用時回空串列（行為跟以前一樣）。"""
    if not mux_enabled():
        return []
    # %C＝本機／目標／埠／帳號的雜湊：路徑短（unix socket 有長度上限），帳號不同各一條
    return ["-o", "ControlMaster=auto",
            "-o", f"ControlPath={os.path.join(mux_dir(), '%C')}",
            "-o", f"ControlPersist={PERSIST_S}s",
            # 共用連線半路斷了要快點發現：ConnectTimeout 管不到已建立的共用連線，
            # 沒有 keepalive 的話後面每條指令都要等到整支逾時才失敗
            "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3"]


def is_preauth_reset(err: str | None) -> bool:
    e = (err or "").lower()
    if "client_loop" in e:
        return False                      # 登入後才斷：不是這一類，也不可重送
    return any(p in e for p in PREAUTH_PATTERNS) or bool(_PREAUTH_RESET_RE.search(e))


def explain(err: str | None) -> str | None:
    """ssh 的 stderr → 給人看的一句。登入前被斷線換成 PREAUTH_MSG（附原文）；其餘回 None。"""
    if err and PREAUTH_MSG in err:
        return " ".join(err.split())[:400]       # 已經是這裡講過的話（重試完的結論），不要再包一層
    if not is_preauth_reset(err):
        return None
    raw = " ".join((err or "").split())[:200]
    return f"{PREAUTH_MSG}。原始訊息：{raw}"


def recent_preauth_failure(host: str, since: float | None = None) -> str | None:
    """這台最近（since 之後；沒給＝COOLDOWN_S 內）有沒有「重試完仍在登入前被斷線」。回訊息。"""
    got = _preauth_fail.get(host)
    if not got:
        return None
    ts, msg = got
    if since is not None:
        return msg if _preauth_seen.get(host, ts) >= since else None
    return msg if _clock() - ts < COOLDOWN_S else None


def reset() -> None:
    """清掉冷卻紀錄（測試用；也給「我確定對方放行了，馬上再試」的入口）。"""
    _preauth_fail.clear()
    _preauth_seen.clear()


def run(argv: list[str], host: str, *, timeout: float) -> subprocess.CompletedProcess:
    """跑一條 ssh 指令（argv 已組好、含 mux_opts）。同一台一次一條；登入前被斷線就退避重試。

    TimeoutExpired／OSError 照舊往外丟（呼叫端本來就各自處理）。
    """
    with host_lock(host):
        cached = recent_preauth_failure(host)
        if cached:
            _preauth_seen[host] = _clock()
            # 剛才才重試到放棄：不再連（再連只會繼續被擋，還可能被 IPS 記成攻擊）
            return subprocess.CompletedProcess(argv, 255, stdout="",
                                               stderr=f"{cached}（{COOLDOWN_S} 秒內不再重連）")
        r = None
        for attempt in range(len(BACKOFF_S) + 1):
            if attempt:
                _sleep(BACKOFF_S[attempt - 1])
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            if not (r.returncode == 255 and is_preauth_reset(r.stderr)):
                _preauth_fail.pop(host, None)
                _preauth_seen.pop(host, None)
                return r
        msg = f"{explain(r.stderr)}（已重試 {len(BACKOFF_S)} 次）"
        _preauth_fail[host] = (_clock(), msg)
        _preauth_seen[host] = _preauth_fail[host][0]
        return subprocess.CompletedProcess(argv, 255, stdout=r.stdout or "", stderr=msg)
