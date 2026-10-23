"""M2 第一片 — 服務發現：一台主機上「跑著什麼服務」。

跟 facts_collector 同一套模式（runner(host, cmd)->str 可注入、per-platform 指令集），
差別在收的東西：facts 收「這台機器是什麼」，這裡收「這台機器在提供什麼」。

## 三個來源，強度不同（畫面必須讓人看得出差別）

| 來源 | 拿得到什麼 | 需要權限 | 可信度 |
|---|---|---|---|
| `ss -tlnp` / `netstat -tlnp` | 監聽埠 + **行程名** | 行程名需 root | 高（機器自己說的） |
| `ss -tln`（退回版） | 只有監聽埠 | 一般帳號即可 | 高，但不知道是誰在聽 |
| `systemctl list-units` | 服務單元名 | 一般帳號即可 | 高（但不含埠） |
| port → 服務猜測表 | 「3306 大概是 MySQL」 | 不必登入 | **猜的**，一律標明 |

⚠️ 收集帳號 `webit3scan` 是唯讀非 root，sudo 白名單只開 `/sys/class/dmi/id/*`
（見 onboard_engine 的納管腳本）——所以**實務上多半拿不到行程名**，只有埠。
這不是 bug，是刻意不為了好看的欄位去擴權；拿不到就留空、由畫面顯示「未知（需 root）」，
不用 port 猜測去填充 process 欄位假裝收到了。

## 為什麼不直接用 nmap 掃埠

外部掃埠只看得到「對外開著的埠」，看不到只綁 127.0.0.1 的服務，也拿不到行程/單元名。
主機自己講的才是事實；外部掃描是佐證（M1 的 net_scan 已經在做）。
"""
from __future__ import annotations

import re

# 監聽埠 → 服務種類。**這是猜測**，只用來當畫面上的提示，
# 絕不寫進 process 欄位（那欄只放機器真的講出來的行程名）。
PORT_SERVICE_GUESS = {
    20: "FTP-data", 21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP",
    53: "DNS", 80: "HTTP", 88: "Kerberos", 110: "POP3", 111: "RPC portmapper",
    123: "NTP", 135: "MS RPC", 139: "NetBIOS", 143: "IMAP", 161: "SNMP",
    389: "LDAP", 443: "HTTPS", 445: "SMB", 465: "SMTPS", 514: "Syslog",
    587: "SMTP submission", 623: "IPMI", 636: "LDAPS", 873: "rsync",
    993: "IMAPS", 995: "POP3S", 1433: "MSSQL", 1521: "Oracle",
    2049: "NFS", 2181: "ZooKeeper", 2375: "Docker", 2376: "Docker(TLS)",
    3000: "Web(node/Grafana)", 3128: "Squid", 3268: "LDAP GC", 3306: "MySQL/MariaDB",
    3389: "RDP", 4369: "Erlang EPMD", 5000: "HTTP(Flask)", 5432: "PostgreSQL",
    5601: "Kibana", 5672: "RabbitMQ", 5900: "VNC", 5985: "WinRM", 5986: "WinRM(TLS)",
    6379: "Redis", 8000: "HTTP API(uvicorn)", 8080: "HTTP(alt)", 8443: "HTTPS(alt)",
    9000: "HTTP(alt)", 9090: "Prometheus/metrics", 9092: "Kafka", 9200: "Elasticsearch",
    11211: "Memcached", 27017: "MongoDB",
}

# 這些埠是「管理/基礎設施」而非業務服務。不過濾掉（照收），只標記，
# 讓畫面可以一鍵收合——判斷哪些算雜訊是看的人的事，不是採集器該替他決定的。
INFRA_PORTS = {22, 53, 123, 161, 323, 111, 5985, 5986, 3389}

LINUX_CMDS = {
    # -H（去表頭）舊版 ss 沒有，所以照收表頭、由解析器跳過。
    # -p 要 root 才有行程名；沒權限時 ss 仍會正常輸出其餘欄位，不會整個失敗。
    #
    # 2026-10-22 方案 B（觀察到的連線推系統上下游）：-tlnp → -tanp（l＝只列監聽，a＝全部狀態），
    # 再用 grep 只留 LISTEN／ESTAB 兩種，TIME_WAIT 之類不傳回來。仍是同一條唯讀指令、不加 sudo；
    # 監聽埠的解析不變（parse_listen 只取 LISTEN 列），ESTAB 列交給 parse_connections。
    "listen": ("{ ss -tanp 2>/dev/null || netstat -tanp 2>/dev/null || ss -tan 2>/dev/null; } "
               "| grep -Ei 'listen|estab'"),
    "units": "systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null",
}

AIX_CMDS = {  # 2026-09-22 B-24 補齊解析，仍未在真機驗證（公司設備，家裡沒有 AIX）
    # AIX 沒有 ss，netstat 也沒有 -p（拿不到行程名）。-Aan 的第一欄是 PCB 位址，
    # 解析器有專門的分支吃它（見 parse_listen）。
    # 2026-10-22：grep 多留 ESTABLISHED（同一份 netstat 輸出，沒有多送指令），給 parse_connections。
    "listen": "netstat -Aan 2>/dev/null | grep -Ei 'listen|estab'",
    # 只 grep active 會把「有這個子系統但沒在跑」整批藏起來 -- 那正是要有人看到的。
    # 照收全部，由 parse_units 只取 active 的名字（狀態欄在最後一欄）。
    "units": "lssrc -a 2>/dev/null",
}

# ⚠️ Windows 的正解**不在這裡**：走 WinRM（winrm_collector.collect_services），
# 不走 SSH。理由見 winrm_collector 檔頭的 2026-07-19 定案——走 WinRM 納管的機器
# 根本沒裝 OpenSSH，硬用 SSH 這條必定連不上，而且幾千台不可能逐台裝。
# service_inventory 會在 platform=windows 時自動分流到 WinRM。
#
# 下面這組只在「呼叫端明確注入 runner」時才會用到（例如已經有 SSH 管道的特殊環境
# 或測試）。保留但不是預設路徑。
WINDOWS_CMDS = {  # 非預設路徑，未在真機驗證
    "listen": ("powershell -NoProfile -Command \"Get-NetTCPConnection -State Listen | "
               "ForEach-Object { $p=(Get-Process -Id $_.OwningProcess -EA SilentlyContinue).ProcessName; "
               "\\\"$($_.LocalAddress):$($_.LocalPort) $p\\\" }\""),
    "units": ("powershell -NoProfile -Command \"Get-Service | Where-Object Status -eq 'Running' | "
              "ForEach-Object { $_.Name }\""),
}

PLATFORMS = {
    "linux": {"cmds": LINUX_CMDS, "tested": True},
    "aix": {"cmds": AIX_CMDS, "tested": False},
    "windows": {"cmds": WINDOWS_CMDS, "tested": False},
}


def split_addr(addr: str) -> tuple[str | None, int | None]:
    """'0.0.0.0:22' / '[::]:80' / '*:443' / '127.0.0.1:33060' -> (bind, port)。

    ⚠️ AIX 的 `netstat` **用點分隔埠號**，不是冒號：`*.22`、`127.0.0.1.32768`、
    IPv6 是 `::1.22`。只切冒號的話，AIX 每一列都解析失敗 -> 服務數 0
    （2026-09-22 B-24：使用者看到的「服務 0」有一半是這裡）。
    """
    if not addr:
        return None, None
    host, sep, port = addr.rpartition(":")
    if not sep or not port.isdigit():
        # AIX 形式：埠號在最後一個點後面（`::1.22` 也走這條，前面的冒號是 v6 位址）
        host, sep, port = addr.rpartition(".")
    host = host.strip("[]") or "*"
    try:
        return host, int(port)
    except ValueError:
        return host, None


def exposure_of(bind: str | None) -> str:
    """綁在哪決定「誰連得到」——這是判斷風險與依賴關係的關鍵，不能只看埠號。

    all       0.0.0.0 / :: / *  → 任何網路都連得到
    localhost 127.0.0.1 / ::1   → 只有本機（別台主機不可能依賴它）
    specific  綁特定 IP          → 只有走那張網卡連得到
    """
    if not bind:
        return "unknown"
    if bind in ("0.0.0.0", "::", "*"):
        return "all"
    if bind.startswith("127.") or bind in ("::1", "localhost"):
        return "localhost"
    return "specific"


def _is_pcb(tok: str) -> bool:
    """看起來是 AIX netstat 的 PCB 位址嗎（一長串 16 進位）。"""
    return len(tok) >= 8 and all(c in "0123456789abcdefABCDEF" for c in tok)


_PROC_RE = re.compile(r'"([^"]+)"')                    # ss:  users:(("sshd",pid=…))
_NETSTAT_PROC_RE = re.compile(r"(\d+)/(\S+)\s*$")      # netstat: 1234/sshd


def parse_listen(text: str) -> list[dict]:
    """解析 ss / netstat 的監聽清單。兩種格式都吃，因為不同發行版裝的不一樣。

    回傳 [{bind, port, proto, process}]，行程名收不到就 None（不猜、不填假值）。
    """
    rows: list[dict] = []
    seen: set[tuple[str, int, str | None]] = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        # 表頭：ss 是 "State Recv-Q…"、netstat 是 "Proto Recv-Q…" 或 "Active Internet…"
        if low.startswith(("state", "proto", "active", "netid")):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue

        proto = "tcp"
        if low.startswith("tcp") or low.startswith("udp"):
            # netstat 格式：proto recv send local foreign STATE [pid/prog]
            proto = "udp" if low.startswith("udp") else "tcp"
            # 只看狀態欄（第 6 欄），不看整行有沒有 listen 字樣：指令改成 -tanp 之後
            # ESTABLISHED 列也會進來，行程名剛好含 listen（例如 xxx-listener）不可以被當成監聽埠。
            if proto == "tcp" and (len(parts) < 6 or parts[5].upper() != "LISTEN"):
                continue          # netstat 會把 established 也印出來，只要 LISTEN
            local = parts[3]
            m = _NETSTAT_PROC_RE.search(line)
            process = m.group(2) if m else None
        elif _is_pcb(parts[0]) and parts[1].lower().startswith(("tcp", "udp")):
            # AIX `netstat -Aan`：第一欄是 PCB 位址（16 進位），格式
            #   f1000e0001d0b3b8 tcp4  0  0  *.22  *.*  LISTEN
            # Linux 的 netstat 第一欄就是 proto，所以上面那條會先接走，不會誤判。
            proto = "udp" if parts[1].lower().startswith("udp") else "tcp"
            if proto == "tcp" and (len(parts) < 7 or parts[6].upper() != "LISTEN"):
                continue
            if len(parts) < 5:
                continue
            local = parts[4]
            # AIX 的 netstat 不印行程名（沒有 -p）。留 None 讓畫面顯示「未知」，
            # 不要拿埠號猜出來的東西填進「行程」欄假裝是事實。
            process = None
        elif parts[0].upper() == "LISTEN" or low.startswith("listen"):
            # ss 格式：State Recv-Q Send-Q Local Peer [Process]
            local = parts[3]
            m = _PROC_RE.search(line)
            process = m.group(1) if m else None
        else:
            continue

        bind, port = split_addr(local)
        if port is None:
            continue
        # ss 會把同一個 sshd 分成 0.0.0.0:22 與 [::]:22 兩行印。不正規化的話
        # 每台機器的服務數都會虛胖一倍，看的人會以為真的多開了服務——
        # 三種萬用位址一律收斂成 0.0.0.0，UNIQUE(ip,proto,port,bind) 才不會因為
        # 這次先看到 v6 就多長一列出來。「只綁 v6」的差別在 exposure 上看不出來，
        # 但那兩者的曝露程度本來就相同（都是任何網路連得到）。
        if bind in ("::", "*", "0.0.0.0"):
            bind = "0.0.0.0"
        key = (bind or "", port, proto)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"bind": bind, "port": port, "proto": proto, "process": process})
    return rows


#: 一台主機一次最多留幾組連線（彙總後）。交易主機一次可能上萬條連線，全收會把 DB 灌爆；
#: 留連線數最多的那幾組，被截掉的筆數照實回報（truncated），不假裝收齊了。
CONN_CAP_PER_HOST = 2000


def _norm_ip(raw: str | None) -> str | None:
    """連線位址的 IP 正規化：去中括號、IPv6 zone（%eth0）、v4-mapped 前綴（::ffff:10.99.1.5）。
    不是合法 IP（* 之類）回 None。"""
    import ipaddress

    v = (raw or "").strip().strip("[]")
    if "%" in v:
        v = v.split("%", 1)[0]
    try:
        a = ipaddress.ip_address(v)
    except ValueError:
        return None
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return str(a)


def is_ignorable_peer(ip: str | None) -> bool:
    """推上下游時不算的位址：本機迴路、link-local、未指定（0.0.0.0／::）。"""
    import ipaddress

    if not ip:
        return True
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return a.is_loopback or a.is_link_local or a.is_unspecified


def parse_connections(text: str) -> list[dict]:
    """從**同一份** ss／netstat 輸出取出已建立的 TCP 連線（ESTAB／ESTABLISHED）。

    三種格式（跟 parse_listen 同一套欄位位置）：
      ss            ESTAB 0 0 <local> <peer> [users:…]
      Linux netstat tcp 0 0 <local> <peer> ESTABLISHED [pid/prog]
      AIX netstat   <PCB> tcp4 0 0 <local> <peer> ESTABLISHED（埠號用點分隔）
    回傳 [{local_ip, local_port, peer_ip, peer_port}]；迴路／link-local 一律丟掉（不是別台）。
    """
    out: list[dict] = []
    for raw in (text or "").splitlines():
        parts = raw.split()
        if len(parts) < 5:
            continue
        head = parts[0].upper()
        if head.startswith("ESTAB"):
            local, peer = parts[3], parts[4]
        elif head.startswith("TCP") and len(parts) >= 6 and parts[5].upper() == "ESTABLISHED":
            local, peer = parts[3], parts[4]
        elif (len(parts) >= 7 and _is_pcb(parts[0]) and parts[1].lower().startswith("tcp")
              and parts[6].upper() == "ESTABLISHED"):
            local, peer = parts[4], parts[5]
        else:
            continue
        lh, lp = split_addr(local)
        ph, pp = split_addr(peer)
        lip, pip = _norm_ip(lh), _norm_ip(ph)
        if lp is None or pp is None or not lip or not pip:
            continue
        if is_ignorable_peer(pip) or is_ignorable_peer(lip):
            continue
        out.append({"local_ip": lip, "local_port": lp, "peer_ip": pip, "peer_port": pp})
    return out


_WILDCARD_BINDS = {None, "", "0.0.0.0", "::", "*"}


def is_inbound(local_ip: str, local_port: int, listens) -> bool:
    """這條連線的本機端是不是自己在聽的位址：埠一樣，**而且**綁的是萬用位址（0.0.0.0／::／*）
    或就是這個本機 IP。只比埠會把「綁在另一張網卡的同號埠」誤判成 inbound。"""
    for bind, port in listens:
        if port != local_port:
            continue
        if bind in _WILDCARD_BINDS or _norm_ip(bind) == local_ip:
            return True
    return False


def summarize_connections(conns: list[dict], listens, cap: int = CONN_CAP_PER_HOST) -> dict:
    """把一次收到的連線彙總成「方向＋對方」。listens＝[(bind, port)]（parse_listen 的 TCP 列）。

    · inbound ＝本機端是自己在聽的位址（is_inbound）：鍵 (local_port, peer_ip)，對方的臨時埠不留
    · outbound＝其餘（本機連出去）：鍵 (peer_ip, peer_port)，本機的臨時埠不留
    臨時埠每條連線都不同，留著只會把一組關係拆成上千列。count＝這次看到幾條連線。
    超過 cap 組時留連線數最多的，truncated＝被截掉幾組。
    """
    listens = list(listens or [])
    agg: dict[tuple, int] = {}
    for c in conns:
        if is_inbound(c["local_ip"], c["local_port"], listens):
            key = ("inbound", c["local_port"], c["peer_ip"], 0)
        else:
            key = ("outbound", 0, c["peer_ip"], c["peer_port"])
        agg[key] = agg.get(key, 0) + 1
    rows = [{"direction": d, "local_port": lp, "peer_ip": ip, "peer_port": pp, "count": n}
            for (d, lp, ip, pp), n in agg.items()]
    rows.sort(key=lambda r: (-r["count"], r["direction"], r["peer_ip"], r["local_port"], r["peer_port"]))
    return {"rows": rows[:cap], "truncated": max(0, len(rows) - cap)}


def parse_units(text: str) -> list[str]:
    """`systemctl list-units --no-legend` 的第一欄就是單元名（sshd.service）。

    Windows 那條指令一行一個服務名，同一支解析器吃得下。

    AIX 的 `lssrc -a` 是表格：`子系統  群組  PID  狀態`，狀態在最後一欄
    （`active`／`inoperative`）。只回 active 的 -- 這份清單的用途是
    「猜某個埠是誰開的」，沒在跑的子系統不可能在聽埠，放進來只會幫倒忙。
    **這不是把 inoperative 藏起來**：那是服務健檢要看的東西，不是這支的職責。
    """
    units: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith(("●", "*")):
            line = line.lstrip("●* ").strip()
        if not line:
            continue
        parts = line.split()
        name = parts[0]
        if name.lower() in ("unit", "load", "listed", "to", "subsystem"):   # legend／表頭殘留
            continue
        if parts[-1].lower() == "inoperative":      # AIX：沒在跑的不列
            continue
        units.append(name)
    return units


def guess_service(port: int, process: str | None, units: list[str] | None = None) -> str | None:
    """給畫面看的「這大概是什麼」。行程名優先（那是事實），埠號其次（那是猜的）。

    回傳值一律只當顯示提示；呼叫端要負責在畫面上標明來源是猜測。
    """
    if process:
        return process
    return PORT_SERVICE_GUESS.get(port)


#: Win32_Service 的 StartMode／State 原文 → 統一小寫英文（畫面再翻中文）
_WIN_START = {"auto": "auto", "manual": "manual", "disabled": "disabled",
              "boot": "boot", "system": "system"}


def parse_windows_services(text: str) -> dict:
    """WinRM SERVICES_PS 的輸出 → {"ports": [...], "winservices": [...], "errors": [...]}。

    · ports：LISTEN 行，交給 parse_listen（跟 Linux 同一支），再補 exposure／guess。
      行程名是「svchost (WinRM)」這種：PID 對得到服務就把服務名接在後面——那是機器講的，
      所以 guess_source=process（確定）。
    · winservices：SVC 行（Win32_Service，有執行狀態）；拿不到時退回 SVCREG 行（登錄檔，
      **state=None**——沒有狀態就是不知道，不可以填 Stopped）。
    · errors：ERR 行（winrm_collector.parse_errors），呼叫端轉成「權限不足（需 X）」。
    舊版腳本（只有 LISTEN 行）也吃得下：winservices 空、errors 空。
    """
    import winrm_collector

    ports = parse_listen(text)
    for s in ports:
        s["exposure"] = exposure_of(s["bind"])
        s["service_guess"] = guess_service(s["port"], s["process"])
        s["guess_source"] = "process" if s["process"] else (
            "port" if s["service_guess"] else None)
        s["is_infra"] = 1 if s["port"] in INFRA_PORTS else 0

    svcs: list[dict] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.rstrip("\r\n")
        if line.startswith("SVC\t"):
            p = (line.split("\t") + [""] * 11)[1:11]
            name, display, state, mode, account, pid, path, delayed, desc, vendor = (x.strip() for x in p)
            src = "cim"
        elif line.startswith("SVCREG\t"):
            p = (line.split("\t") + [""] * 9)[1:9]
            name, display, mode, account, path, delayed, desc, vendor = (x.strip() for x in p)
            state, pid, src = "", "", "registry"
        else:
            continue
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        svcs.append({
            "name": name, "display_name": display or None,
            "state": state.lower() or None,
            "start_mode": _WIN_START.get(mode.lower(), mode.lower() or None),
            "account": account or None,
            "pid": int(pid) if pid.isdigit() and int(pid) > 0 else None,
            "via": src,
            # 2026-10-21 Windows 欄：舊腳本沒有這幾欄 → None（不知道），不是「否」
            "path_name": path or None, "description": desc or None,
            "delayed": (1 if delayed == "1" else 0) if delayed in ("0", "1") else None,
            "vendor": vendor or None,
        })
    return {"ports": ports, "winservices": svcs,
            # IIS 段的錯誤由 iis_inventory 自己講（一句），這裡不重複
            "errors": [e for e in winrm_collector.parse_errors(text.split("@@IIS", 1)[0])]}


def detect_platform(open_ports: list[int] | None) -> str:
    """跟 facts_collector 同一套判準，避免兩邊對同一台猜出不同平台。"""
    import facts_collector

    return facts_collector.detect_platform(open_ports)


def collect(runner, host: str, platform: str = "linux") -> dict:
    """對 host 收服務清單。runner(host, cmd)->str 抽象 SSH（測試可注入）。

    回傳 {"services": [...], "units": [...], "process_visible": bool}。
    process_visible=False 代表「這次收集看不到行程名」（多半是沒有 root），
    畫面要據此顯示「未知（需 root）」而不是留白讓人以為沒服務在跑。
    """
    spec = PLATFORMS.get(platform)
    if not spec:
        raise ValueError(f"未支援的平台：{platform}")

    try:
        listen_raw = runner(host, spec["cmds"]["listen"])
    except Exception as exc:  # noqa: BLE001 - 收不到就誠實回報，不吞成空清單
        raise ConnectionError(f"取得監聽清單失敗：{exc}") from exc
    try:
        units_raw = runner(host, spec["cmds"]["units"])
    except Exception:  # noqa: BLE001 - 單元清單是加分項，拿不到不影響主結果
        units_raw = ""

    services = parse_listen(listen_raw)
    units = parse_units(units_raw)
    for s in services:
        s["exposure"] = exposure_of(s["bind"])
        s["service_guess"] = guess_service(s["port"], s["process"], units)
        # 行程名是機器講的，service_guess 可能只是埠號猜的——把來源記下來，
        # 畫面才有辦法區分「確定是 nginx」跟「3306 所以大概是 MySQL」。
        s["guess_source"] = "process" if s["process"] else (
            "port" if s["service_guess"] else None
        )
        s["is_infra"] = 1 if s["port"] in INFRA_PORTS else 0

    # 已建立的連線：同一份輸出，不多送指令（2026-10-22 方案 B，見 observed_deps）
    listen_tcp = [(s["bind"], s["port"]) for s in services if s["proto"] == "tcp"]
    conns = summarize_connections(parse_connections(listen_raw), listen_tcp)

    return {
        "services": services,
        "units": units,
        "process_visible": any(s["process"] for s in services),
        "connections": conns["rows"],
        "connections_truncated": conns["truncated"],
    }
