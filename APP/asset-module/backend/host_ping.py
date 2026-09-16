"""存活確認（PING）——使用者 2026-09-16：「多一個 PING 按鈕動作，我可確認是否存活」。

## 為什麼不是只跑 ping

ping 不通**不等於**機器不在。ICMP 被防火牆擋是常態（Windows 防火牆預設就擋 echo request，
公司網段政策也常關）。只看 ICMP 會把一整批活著的 Windows 判成死的，比不做還糟。

所以一次做兩件事、結論分開講：

1. **ICMP**：送幾個封包、回幾個、平均幾毫秒
2. **TCP**：試 22/445/3389/5985（`net_scan.PROBE_PORTS`，跟網段掃描同一組）
   ——連得上或被明確拒絕，都代表主機在線

四種結論：

| ICMP | TCP | 結論 | 證據等級 |
|---|---|---|---|
| 有回應 | — | 活著 | 證據 |
| 沒回應 | 有回應 | 活著，ICMP 被擋 | 證據 |
| 沒回應 | 沒回應 | **沒有回應**（關機／不在線／防火牆全擋，分不出來） | 推論 |
| 無法執行 | 任一 | 以 TCP 為準，並說明 ping 為什麼沒跑 | 未驗證 |

「沒有回應」不寫成「機器不存在」——分不出來的事不要講滿（CLAUDE.md：幾分證據說幾分話）。

## 不動資料

只回報、只記進採集紀錄（`collect_log`），**不改資產狀態、不改可信度**。
一次手動 ping 不通就把機器標成失聯太危險（可能只是當下網路抖一下）。

## 安全

- 只接受 IP 位址（沿用 `ssh_probe.validate`），不收主機名 → 參數不可能被塞東西
- 子行程用參數清單呼叫，**不經 shell**；只送 3 個封包，不是掃描
- 不需要 root：Linux 的 `ping_group_range` 允許非特權 ICMP socket；真的不能跑就照實說
"""
from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime

import net_scan
import ssh_probe

#: 送幾個封包。夠判斷通不通，又不會變成壓測
PING_COUNT = 3
#: 每個封包等幾秒
PING_WAIT = 1
#: 整個 ping 行程的上限（秒）——封包間隔 1 秒，留點餘裕
PING_TIMEOUT = PING_COUNT * (PING_WAIT + 1) + 3

_RECEIVED = re.compile(r"(\d+)\s*(?:packets\s+)?received")
_WIN_RECEIVED = re.compile(r"(?:Received|接收)\s*=\s*(\d+)")
_RTT = re.compile(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)")
_WIN_AVG = re.compile(r"(?:Average|平均)\s*=\s*(\d+)\s*ms")


def _cmd(ip: str) -> list[str]:
    """Linux（正式環境）與 Windows（開發機）各自的參數。"""
    if os.name == "nt":
        return ["ping", "-n", str(PING_COUNT), "-w", str(PING_WAIT * 1000), ip]
    # -n 不做反解（省時、也避免 DNS 壞掉時整個卡住）
    return ["ping", "-n", "-c", str(PING_COUNT), "-W", str(PING_WAIT), ip]


def _run(cmd: list[str], timeout: float) -> tuple[int | None, str]:
    """跑 ping 子行程。回 (returncode, 輸出)；跑不起來回 (None, 原因)。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return None, "這台主機上沒有 ping 這個指令"
    except PermissionError as exc:
        return None, f"沒有權限執行 ping：{exc}"
    except subprocess.TimeoutExpired:
        return 124, f"超過 {timeout:.0f} 秒沒有結果，已中止"
    except OSError as exc:
        return None, f"ping 執行失敗：{exc}"
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    return r.returncode, "\n".join(x for x in (out, err) if x)


def _parse(text: str) -> tuple[int | None, float | None]:
    """從 ping 輸出取出「回了幾個」與「平均毫秒」。取不到就 None，不要猜。"""
    received = None
    m = _RECEIVED.search(text) or _WIN_RECEIVED.search(text)
    if m:
        received = int(m.group(1))
    avg = None
    m = _RTT.search(text)
    if m:
        avg = float(m.group(2))
    else:
        m = _WIN_AVG.search(text)
        if m:
            avg = float(m.group(1))
    return received, avg


def icmp(ip: str, runner=_run) -> dict:
    """ICMP 部分。`runner` 抽出來讓測試注入，不用真的打網路。"""
    code, text = runner(_cmd(ip), PING_TIMEOUT)
    if code is None:
        return {"ran": False, "sent": PING_COUNT, "received": None, "avg_ms": None,
                "reason": text, "output": text}
    received, avg = _parse(text)
    if received is None:                       # 有跑但看不懂輸出：用結束碼保底
        received = PING_COUNT if code == 0 else 0
    return {"ran": True, "sent": PING_COUNT, "received": received, "avg_ms": avg,
            "reason": None, "output": text}


def tcp(ip: str, prober=None) -> dict:
    """TCP 部分：跟網段掃描同一組 port，有回應就代表主機在線。"""
    probe = prober or net_scan._probe_host
    open_ports = probe(ip)
    return {"ran": True, "alive": open_ports is not None,
            "open_ports": open_ports or [], "ports_tried": list(net_scan.PROBE_PORTS)}


def verdict(icmp_r: dict, tcp_r: dict) -> tuple[bool | None, str, str]:
    """回 (alive, 白話結論, 證據等級)。分不出來的不要講滿。"""
    ports = "、".join(str(p) for p in tcp_r["open_ports"])
    if icmp_r["ran"] and (icmp_r["received"] or 0) > 0:
        ms = f"，平均 {icmp_r['avg_ms']:.1f} ms" if icmp_r["avg_ms"] is not None else ""
        return True, f"活著：ping 送 {icmp_r['sent']} 個回 {icmp_r['received']} 個{ms}", "證據"
    if tcp_r["alive"]:
        why = f"port {ports} 連得上" if ports else "TCP 有回應（連接埠是關的，但主機有回話）"
        if not icmp_r["ran"]:
            return True, f"活著：{why}（ping 沒跑成——{icmp_r['reason']}）", "證據"
        return True, f"活著：ping 不通但 {why}——這台把 ICMP 擋掉了，不是沒開機", "證據"
    if not icmp_r["ran"]:
        return None, (f"無法判斷：ping 沒跑成（{icmp_r['reason']}），"
                      f"TCP {'、'.join(str(p) for p in tcp_r['ports_tried'])} 也都沒回應"), "未驗證"
    return False, ("沒有回應：ping 不通，TCP "
                   f"{'、'.join(str(p) for p in tcp_r['ports_tried'])} 也都沒回應。"
                   "關機、不在這個網段、或防火牆全擋——這三種分不出來"), "推論"


def check(ip, runner=_run, prober=None) -> dict:
    """單台存活確認。只回報，不改任何資料。"""
    ip, _ = ssh_probe.validate(ip, 22)
    icmp_r = icmp(ip, runner=runner)
    tcp_r = tcp(ip, prober=prober)
    alive, text, level = verdict(icmp_r, tcp_r)
    return {"ip": ip, "alive": alive, "verdict": text, "evidence": level,
            "icmp": icmp_r, "tcp": tcp_r,
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
