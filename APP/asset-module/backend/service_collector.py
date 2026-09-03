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
    "listen": "ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null || ss -tln 2>/dev/null",
    "units": "systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null",
}

AIX_CMDS = {  # 保留機制，未在真機驗證（AIX 沒有 ss/systemd）
    "listen": "netstat -Aan | grep -i listen",
    "units": "lssrc -a 2>/dev/null | grep -i active",
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
    """'0.0.0.0:22' / '[::]:80' / '*:443' / '127.0.0.1:33060' -> (bind, port)。"""
    if not addr:
        return None, None
    host, _, port = addr.rpartition(":")
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
            if "listen" not in low and proto == "tcp":
                continue          # netstat 會把 established 也印出來，只要 LISTEN
            local = parts[3]
            m = _NETSTAT_PROC_RE.search(line)
            process = m.group(2) if m else None
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


def parse_units(text: str) -> list[str]:
    """`systemctl list-units --no-legend` 的第一欄就是單元名（sshd.service）。

    Windows 那條指令一行一個服務名，同一支解析器吃得下。
    """
    units: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith(("●", "*")):
            line = line.lstrip("●* ").strip()
        if not line:
            continue
        name = line.split()[0]
        if name.lower() in ("unit", "load", "listed", "to"):   # legend 殘留
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

    return {
        "services": services,
        "units": units,
        "process_visible": any(s["process"] for s in services),
    }
