"""2-1 開始收集的探測器：同一台的幾個埠**同時**連，不是一個一個等（v1.468.5）。

2026-10-19 使用者：「收集一次要很久，這有什麼改善方式」。221 實測 254 個位址 13.7 秒，
其中 13.2 秒在探測；探測裡大部分是**空位址**：net_scan._probe_host 對 22/445/3389/5985
一個埠等 0.6 秒逾時再換下一個，四個埠 2.4 秒，再補一次 ICMP（1 秒）＝ 每個空位址約 3.4 秒。

這裡把四個埠的 connect 同時發出去、一起等 0.6 秒——送出去的封包**一模一樣**
（每台還是 4 個 SYN、TCP 全逾時才補 1 個 ICMP），只是不再排隊等。空位址約 1.6 秒。

判定規則刻意跟 net_scan._probe_host 完全一致（存活判定要跟掃描一致，不要兩套結論打架）：
  - connect 成功 → 開放埠、活著
  - 被拒（ECONNREFUSED）→ 主機在、埠關，活著
  - 逾時／其他錯誤 → 不改變 alive
  - TCP 全無反應 → 補一次 ICMP，ping 得通回 []（活著但沒有開放埠），否則 None
"""
from __future__ import annotations

import selectors
import socket
import time

#: 被拒＝主機在、埠關（Linux 111／Windows 10061）
_REFUSED = (111, 10061)
#: 非阻塞 connect 進行中（Linux EINPROGRESS 115／EAGAIN 11／Windows WSAEWOULDBLOCK 10035）
_IN_PROGRESS = (115, 11, 10035, 36)


def probe_host(ip: str, ports, timeout: float = 0.6, icmp: bool = True,
               ping=None) -> list[int] | None:
    """回這台開放的埠（照 ports 順序）；完全沒反應回 None。ping 可注入（測試）。"""
    order = {p: i for i, p in enumerate(ports)}
    open_ports: list[int] = []
    alive = False
    pending: dict[socket.socket, int] = {}
    try:
        for port in ports:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            except OSError:
                continue        # 檔案描述子用完之類：這個埠當沒回應，不讓整輪收集失敗
            s.setblocking(False)
            try:
                rc = s.connect_ex((ip, port))
            except OSError:
                s.close()
                continue
            if rc == 0:
                open_ports.append(port)
                alive = True
                s.close()
            elif rc in _REFUSED:
                alive = True
                s.close()
            elif rc in _IN_PROGRESS:
                pending[s] = port
            else:
                s.close()       # 例如網路不可達：同 _probe_host，不算活著
        deadline = time.monotonic() + timeout
        # selectors（Linux 上是 epoll）：不像 select() 受 fd 編號 < 1024 限制，
        # 並行數調高時不會變成「看起來逾時」的假結果。
        sel = selectors.DefaultSelector()
        try:
            for s in pending:
                sel.register(s, selectors.EVENT_WRITE)
            while pending:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                events = sel.select(left)
                if not events:
                    break       # 逾時
                for key, _mask in events:
                    s = key.fileobj
                    port = pending.pop(s, None)
                    if port is None:
                        continue
                    sel.unregister(s)
                    try:
                        err = s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                    except OSError:
                        err = -1
                    if err == 0:
                        open_ports.append(port)
                        alive = True
                    elif err in _REFUSED:
                        alive = True
                    s.close()
        finally:
            sel.close()
    finally:
        for s in pending:
            s.close()
    if alive:
        return sorted(open_ports, key=order.get)
    if icmp:
        if ping is None:
            import net_scan
            ping = net_scan.host_ping_alive
        if ping(ip):
            return []
    return None
