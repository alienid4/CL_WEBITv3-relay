"""網卡（NIC）與 FC HBA 明細採集：已納管主機用唯讀指令撈每張卡的識別與速率。

2026-09-14 機房搬遷盤點。跟 host_spec 同一趟收集（共用 runner），全部唯讀、非 root。

重點（使用者提的）：速率要分「目前協商速率」與「額定/最大速率」兩欄——10G 卡因交換器或
線材降速跑 1G 很常見（HBA 同理，16G 跑 8G）。目前 < 額定就標 downgraded。

⚠️ Linux 格式標準、可信；**AIX 依文件撰寫、尚未實機驗過**。收不到的欄位留白。
Fabric/SAN switch/zone/target 這幾欄由 SAN 收集反查補（下一刀），這裡先留空。
"""
from __future__ import annotations

import json
import re

# ---- 指令（唯讀、非 root）----
CMD_LINUX = {
    # -d -o：每個介面一行、含 vlan/bond 明細
    "iplink": "ip -d -o link show 2>/dev/null",
    "ipaddr": "ip -o -4 addr show 2>/dev/null",
    "iproute": "ip route show default 2>/dev/null",
    # 目前協商速率（Mb/s；-1＝down/未知）
    "speeds": "grep -H . /sys/class/net/*/speed 2>/dev/null",
    # 額定/最大速率：ethtool 的 Supported link modes（免 root；可能未裝 → 空）
    "ethtool": 'for i in $(ls /sys/class/net 2>/dev/null); do echo "@@IF $i"; ethtool "$i" 2>/dev/null; done',
    # FC HBA：一次把每個 fc_host 的欄位倒出來
    "fc": ('for h in /sys/class/fc_host/*; do [ -e "$h" ] || continue; '
           'echo "@@FC $(basename "$h")"; '
           'for k in port_name node_name speed supported_speeds port_state; do '
           'echo "$k=$(cat "$h/$k" 2>/dev/null)"; done; done'),
    # 網卡驅動＋PCI 位置（走 sysfs，免裝任何工具）——當作「晶片」的免裝來源
    "driver": ('for i in $(ls /sys/class/net 2>/dev/null); do echo "@@IF $i"; '
               'echo "driver=$(basename "$(readlink /sys/class/net/$i/device/driver 2>/dev/null)" 2>/dev/null)"; '
               'echo "bus=$(basename "$(readlink /sys/class/net/$i/device 2>/dev/null)" 2>/dev/null)"; done'),
    # 網卡型號（有裝 lspci 才有，對得到 bus 才填；沒裝就用上面的 driver 當晶片線索）
    "lspci": "lspci -D 2>/dev/null || /usr/sbin/lspci -D 2>/dev/null || true",
}
CMD_AIX = {
    "netstat": "netstat -in 2>/dev/null",
    "ent": 'for e in $(lsdev -Cc adapter 2>/dev/null | grep -i "^ent" | awk "{print \\$1}"); do echo "@@ENT $e"; entstat -d "$e" 2>/dev/null; done',
    "fcs": 'for f in $(lsdev -Cc adapter 2>/dev/null | grep -i "^fcs" | awk "{print \\$1}"); do echo "@@FCS $f"; lscfg -vpl "$f" 2>/dev/null; fcstat "$f" 2>/dev/null; done',
}

_SKIP_IF = ("lo", "veth", "docker", "virbr", "br-", "tun", "tap", "cali", "flannel", "kube", "cni")


def _skip(name: str) -> bool:
    return any(name == s or name.startswith(s) for s in _SKIP_IF)


def _fmt_wwn(raw: str) -> str | None:
    """0x10000000c9abcdef → 10:00:00:00:c9:ab:cd:ef。認不出回原字串/None。"""
    h = (raw or "").strip().lower().replace("0x", "")
    if not re.fullmatch(r"[0-9a-f]{16}", h):
        return raw.strip() or None
    return ":".join(h[i:i + 2] for i in range(0, 16, 2))


# ---- Linux 解析 ----
def parse_iplink(text: str) -> dict:
    """ip -d -o link → {name: {mac, state, master, vlan}}。"""
    out: dict = {}
    for ln in text.splitlines():
        m = re.match(r"\d+:\s*([^:@]+)[@:]", ln)
        if not m:
            continue
        name = m.group(1).strip()
        if _skip(name):
            continue
        rec: dict = {}
        mac = re.search(r"link/ether\s+([0-9a-f:]{17})", ln)
        if mac:
            rec["mac"] = mac.group(1)
        st = re.search(r"\bstate\s+(\w+)", ln)
        rec["state"] = st.group(1).lower() if st else None
        mst = re.search(r"\bmaster\s+(\S+)", ln)
        if mst:
            rec["master"] = mst.group(1)
        vl = re.search(r"\bvlan\b.*?\bid\s+(\d+)", ln)
        if vl:
            rec["vlan"] = vl.group(1)
        out[name] = rec
    return out


def parse_ipaddr(text: str) -> dict:
    """ip -o -4 addr → {dev: {ip, subnet}}（subnet 存 CIDR 前綴，如 24）。"""
    out: dict = {}
    for ln in text.splitlines():
        p = ln.split()
        if len(p) < 4 or "inet" not in p:
            continue
        dev = p[1]
        cidr = p[p.index("inet") + 1]
        ip, _, pref = cidr.partition("/")
        out.setdefault(dev, {"ip": ip, "subnet": pref or None})
    return out


def parse_iproute(text: str) -> dict:
    """ip route show default → {dev: gateway}（含一個全域 default）。"""
    out: dict = {}
    for ln in text.splitlines():
        via = re.search(r"default\s+via\s+(\S+)", ln)
        dev = re.search(r"\bdev\s+(\S+)", ln)
        if via and dev:
            out[dev.group(1)] = via.group(1)
        elif via:
            out["*"] = via.group(1)
    return out


def parse_speeds(text: str) -> dict:
    """/sys/class/net/*/speed → {name: Mb/s}（略過 -1）。"""
    out: dict = {}
    for ln in text.splitlines():
        m = re.match(r".*/net/([^/]+)/speed:(-?\d+)", ln)
        if m and int(m.group(2)) > 0:
            out[m.group(1)] = int(m.group(2))
    return out


def parse_ethtool_max(text: str) -> dict:
    """ethtool 的 Supported link modes → {name: 最大 Mb/s}。"""
    out: dict = {}
    cur = None
    collecting = False
    speeds: list[int] = []
    def flush():
        if cur and speeds:
            out[cur] = max(speeds)
    for ln in text.splitlines():
        if ln.startswith("@@IF "):
            flush()
            cur = ln[5:].strip()
            speeds = []
            collecting = False
            continue
        if "Supported link modes:" in ln:
            collecting = True
        elif collecting and re.match(r"\s*[A-Za-z]", ln) and "base" not in ln.lower():
            collecting = False   # 下一個欄位開始
        if collecting:
            for n in re.findall(r"(\d+)base", ln):
                speeds.append(int(n))
    flush()
    return out


def parse_driver(text: str) -> dict:
    """sysfs 倒出的 @@IF/driver/bus → {name: {driver, bus}}。免裝任何工具。"""
    out: dict = {}
    cur = None
    for ln in text.splitlines():
        if ln.startswith("@@IF "):
            cur = ln[5:].strip()
            out[cur] = {}
        elif cur and "=" in ln:
            k, v = ln.split("=", 1)
            v = v.strip()
            if v:
                out[cur][k.strip()] = v
    return out


def parse_lspci_bus_model(text: str) -> dict:
    """lspci -D → {完整 bus(0000:3b:00.0): 型號}。用來把網卡 bus 對成人看得懂的型號。"""
    out: dict = {}
    for ln in text.splitlines():
        m = re.match(r"([0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.\d)\s+[^:]+:\s*(.+)", ln, re.I)
        if m:
            out[m.group(1).lower()] = m.group(2).strip()
    return out


def parse_fc(text: str) -> list[dict]:
    """Linux fc_host 倒出的 key=value 區塊 → 每個 HBA 一筆。"""
    out: list[dict] = []
    cur: dict | None = None
    for ln in text.splitlines():
        if ln.startswith("@@FC "):
            cur = {"hba_name": ln[5:].strip()}
            out.append(cur)
        elif cur is not None and "=" in ln:
            k, v = ln.split("=", 1)
            k, v = k.strip(), v.strip()
            if k == "port_name":
                cur["wwpn"] = _fmt_wwn(v)
            elif k == "node_name":
                cur["wwnn"] = _fmt_wwn(v)
            elif k == "speed":
                m = re.search(r"(\d+)", v)
                cur["speed_cur"] = int(m.group(1)) if m else None
            elif k == "supported_speeds":
                nums = [int(x) for x in re.findall(r"(\d+)", v)]
                cur["speed_max"] = max(nums) if nums else None
            elif k == "port_state":
                cur["port_state"] = v or None
    return [h for h in out if h.get("wwpn") or h.get("hba_name")]


# ---- AIX 解析（⚠️ 未實機驗）----
def parse_aix_ent(text: str) -> list[dict]:
    """entstat -d 逐 adapter：目前速率 Running、額定看 Media Speed 支援清單、實體位址。"""
    out: list[dict] = []
    cur: dict | None = None
    for ln in text.splitlines():
        if ln.startswith("@@ENT "):
            cur = {"nic_name": ln[6:].strip()}
            out.append(cur)
        elif cur is not None:
            mac = re.search(r"Hardware Address:\s*([0-9a-f:]{17})", ln, re.I)
            if mac:
                cur["mac"] = mac.group(1)
            run = re.search(r"Media Speed Running:\s*(\d+)", ln)
            if run:
                cur["speed_cur"] = int(run.group(1))
            sel = re.search(r"Media Speed (?:Selected|Adapter).*?(\d+)", ln)
            if sel and "speed_max" not in cur:
                cur["speed_max"] = int(sel.group(1))
    return [n for n in out if n.get("nic_name")]


def parse_aix_fcs(text: str) -> list[dict]:
    """lscfg -vpl fcsX（Network Address＝WWPN）＋ fcstat（速率/狀態）。"""
    out: list[dict] = []
    cur: dict | None = None
    for ln in text.splitlines():
        if ln.startswith("@@FCS "):
            cur = {"hba_name": ln[6:].strip()}
            out.append(cur)
        elif cur is not None:
            na = re.search(r"Network Address\.+([0-9A-Fa-f]{16})", ln)
            if na:
                cur["wwpn"] = _fmt_wwn(na.group(1))
            run = re.search(r"Running Speed:\s*(\d+)\s*GBIT", ln, re.I)
            if run:
                cur["speed_cur"] = int(run.group(1))
            st = re.search(r"Port Speed \(running\):\s*(\d+)", ln, re.I)
            if st:
                cur["speed_cur"] = int(st.group(1))
            state = re.search(r"Attention Type:\s*(.+)", ln)
            if state:
                cur["port_state"] = state.group(1).strip()
    return [h for h in out if h.get("wwpn") or h.get("hba_name")]


def _mk_sources(rec: dict) -> str:
    """每個有值的欄位標來源＝collect（slice 2a 全是機器收的）。"""
    return json.dumps({k: "collect" for k, v in rec.items()
                       if v not in (None, "") and k not in ("nic_name", "hba_name")},
                      ensure_ascii=False)


def collect_net(runner, host: str, platform: str) -> dict:
    """回 {nics:[...], hbas:[...], raw:{}}。收不到就空。"""
    raw: dict = {}
    nics: list[dict] = []
    hbas: list[dict] = []
    if platform == "aix":
        for k, cmd in CMD_AIX.items():
            raw[k] = runner(host, cmd)
        nics = parse_aix_ent(raw.get("ent", ""))
        hbas = parse_aix_fcs(raw.get("fcs", ""))
    else:
        for k, cmd in CMD_LINUX.items():
            raw[k] = runner(host, cmd)
        links = parse_iplink(raw.get("iplink", ""))
        addrs = parse_ipaddr(raw.get("ipaddr", ""))
        routes = parse_iproute(raw.get("iproute", ""))
        cur = parse_speeds(raw.get("speeds", ""))
        mx = parse_ethtool_max(raw.get("ethtool", ""))
        drv = parse_driver(raw.get("driver", ""))
        bus2model = parse_lspci_bus_model(raw.get("lspci", ""))
        gw_default = routes.get("*")
        for name, li in links.items():
            d = drv.get(name, {})
            model = bus2model.get((d.get("bus") or "").lower())   # lspci 有才有型號
            rec = {
                "nic_name": name, "mac": li.get("mac"), "state": li.get("state"),
                "ip": addrs.get(name, {}).get("ip"),
                "subnet": addrs.get(name, {}).get("subnet"),
                "gateway": routes.get(name) or (gw_default if addrs.get(name) else None),
                "vlan": li.get("vlan"), "bond": li.get("master"),
                "speed_cur": cur.get(name), "speed_max": mx.get(name),
                "driver": d.get("driver"),         # sysfs，免裝
                "model": model,                    # lspci，有裝才有
            }
            nics.append(rec)
        hbas = parse_fc(raw.get("fc", ""))
    # 降速旗標（NIC/HBA 同理）
    for rec in nics + hbas:
        c, m = rec.get("speed_cur"), rec.get("speed_max")
        rec["downgraded"] = 1 if (c and m and c < m) else 0
    return {"nics": nics, "hbas": hbas, "raw": raw}


def store_net(conn, asset_serial: str, res: dict) -> dict:
    """把 NIC/HBA 明細落庫。以自然鍵 upsert（機器欄更新、留著人填欄——slice 2a 尚無人填欄）。"""
    now = "datetime('now','localtime')"
    n_nic = n_hba = 0
    for r in res.get("nics", []):
        if not r.get("nic_name"):
            continue
        conn.execute(
            "INSERT INTO asset_nic (asset_serial, nic_name, mac, ip, subnet, gateway, vlan, "
            "bond, bond_mode, speed_cur, speed_max, downgraded, state, driver, model, sources, collected_at) "
            f"VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, {now}) "
            "ON CONFLICT(asset_serial, nic_name) DO UPDATE SET "
            "mac=excluded.mac, ip=excluded.ip, subnet=excluded.subnet, gateway=excluded.gateway, "
            "vlan=excluded.vlan, bond=excluded.bond, speed_cur=excluded.speed_cur, "
            "speed_max=excluded.speed_max, downgraded=excluded.downgraded, state=excluded.state, "
            f"driver=excluded.driver, model=excluded.model, sources=excluded.sources, collected_at={now}",
            (asset_serial, r.get("nic_name"), r.get("mac"), r.get("ip"), r.get("subnet"),
             r.get("gateway"), r.get("vlan"), r.get("bond"), r.get("bond_mode"),
             r.get("speed_cur"), r.get("speed_max"), r.get("downgraded", 0), r.get("state"),
             r.get("driver"), r.get("model"), _mk_sources(r)),
        )
        n_nic += 1
    for r in res.get("hbas", []):
        if not (r.get("hba_name") or r.get("wwpn")):
            continue
        conn.execute(
            "INSERT INTO asset_hba (asset_serial, hba_name, wwpn, wwnn, speed_cur, speed_max, "
            "downgraded, port_state, sources, collected_at) "
            f"VALUES (?,?,?,?,?,?,?,?,?, {now}) "
            "ON CONFLICT(asset_serial, hba_name) DO UPDATE SET "
            "wwpn=excluded.wwpn, wwnn=excluded.wwnn, speed_cur=excluded.speed_cur, "
            "speed_max=excluded.speed_max, downgraded=excluded.downgraded, "
            f"port_state=excluded.port_state, sources=excluded.sources, collected_at={now}",
            (asset_serial, r.get("hba_name"), r.get("wwpn"), r.get("wwnn"),
             r.get("speed_cur"), r.get("speed_max"), r.get("downgraded", 0), r.get("port_state"),
             _mk_sources(r)),
        )
        n_hba += 1
    conn.commit()
    return {"nics": n_nic, "hbas": n_hba}
