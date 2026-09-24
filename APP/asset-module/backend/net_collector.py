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
# AIX（2026-09-22 B-24 改寫）
#
# ⚠️ 原本這組主力是 `entstat -d`／`fcstat`。**這兩支多半要 root**，
# 收集帳號是唯讀非 root，於是每一台 AIX 的網卡與 HBA 都是空的——
# 而畫面上「沒權限問到」跟「這台沒有網卡」長得一模一樣。
#
# 改成以「非 root 讀得到的 ODM 查詢」為主力：
#   lsdev  有哪些介面卡（ent0／fcs0／vfchost…）  <- 這一條決定「到底有沒有」
#   netstat -in / ifconfig -a  介面、MAC、IP、遮罩
#   lsattr -El  卡的設定值（media_speed 等）
#   lscfg -vpl  卡的型號、位置，FC 的 Network Address（＝WWPN）
# entstat／fcstat 降級成「加分項」：問得到就補目前速率，問不到不影響主結果。
CMD_AIX = {
    # 這一條是判斷「有沒有」的依據。它空的話，後面全部不可以說「沒有」，
    # 只能說「問不到」——兩者差別見 collect_net 的 notes。
    "lsdev": "lsdev -Cc adapter 2>/dev/null",
    "netstat": "netstat -in 2>/dev/null",
    "ifconfig": "ifconfig -a 2>/dev/null",
    "route": "netstat -rn 2>/dev/null",
    # 每張乙太卡：設定值（media_speed）＋型號位置。都是 ODM 查詢，非 root 讀得到。
    "entinfo": ('for e in `lsdev -Cc adapter 2>/dev/null | grep "^ent" | awk \'{print $1}\'`; do '
                'echo "@@ENT $e"; lsattr -El "$e" 2>/dev/null; lscfg -vl "$e" 2>/dev/null; done'),
    # 每張 FC 卡：WWPN（lscfg 的 Network Address）＋設定值。
    # vfchost/fcs 都收——LPAR 上 NPIV 的虛擬 FC 一樣是這台的 HBA。
    "fcsinfo": ('for f in `lsdev -Cc adapter 2>/dev/null | grep -E "^(fcs|vfchost)" | awk \'{print $1}\'`; do '
                'echo "@@FCS $f"; lscfg -vpl "$f" 2>/dev/null; lsattr -El "$f" 2>/dev/null; done'),
    # 加分項：目前協商速率。多半要 root，拿不到就算了（不影響上面的主結果）。
    "entstat": ('for e in `lsdev -Cc adapter 2>/dev/null | grep "^ent" | awk \'{print $1}\'`; do '
                'echo "@@ENT $e"; entstat -d "$e" 2>/dev/null; done'),
    "fcstat": ('for f in `lsdev -Cc adapter 2>/dev/null | grep "^fcs" | awk \'{print $1}\'`; do '
               'echo "@@FCS $f"; fcstat "$f" 2>/dev/null; done'),
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


def parse_aix_lsdev(text: str) -> dict[str, list[str]]:
    """`lsdev -Cc adapter` -> {"ent": [...], "fcs": [...]}。

    這支的回傳是**「到底有沒有」的唯一依據**：它列得出 fcs0 而我們拿不到 WWPN，
    那是權限問題；它一張 FC 都沒列，那才是「這台真的沒有 HBA」。
    兩者在畫面上必須講不同的話。
    """
    out: dict[str, list[str]] = {"ent": [], "fcs": []}
    for ln in (text or "").splitlines():
        parts = ln.split()
        if not parts:
            continue
        name = parts[0]
        if name.startswith("ent") and name[3:].isdigit():
            out["ent"].append(name)
        elif name.startswith("fcs") and name[3:].isdigit():
            out["fcs"].append(name)
        elif name.startswith("vfchost"):
            out["fcs"].append(name)
    return out


def _aix_mac(raw: str) -> str | None:
    """AIX 的 MAC 是點分隔而且**不補零**：`fa.ce.0.1.2.3` -> `fa:ce:00:01:02:03`。

    不補零就直接存的話，同一張卡在 Linux 側（RVTools／掃描）收到的
    `FA:CE:00:01:02:03` 會被當成另一張卡，機器比對就對不起來。
    """
    import re as _re

    t = (raw or "").strip().lower()
    if not _re.fullmatch(r"[0-9a-f]{1,2}(\.[0-9a-f]{1,2}){5}", t):
        return None
    return ":".join(p.zfill(2) for p in t.split("."))


def parse_aix_netstat_in(text: str) -> dict[str, dict]:
    """`netstat -in` -> {介面: {mac, ip}}。

    每個介面**兩列**：`link#` 那列有 MAC，網段那列有 IP。
    介面叫 en0，底層的卡叫 ent0 —— 兩者要對起來，不然畫面上會變成兩張卡。
    """
    out: dict[str, dict] = {}
    for ln in (text or "").splitlines():
        parts = ln.split()
        if len(parts) < 4 or parts[0].lower() in ("name",):
            continue
        name = parts[0].rstrip("*")          # 介面 down 時 AIX 會加星號
        rec = out.setdefault(name, {})
        if parts[2].startswith("link#"):
            mac = _aix_mac(parts[3])
            if mac:
                rec["mac"] = mac
        elif parts[3].count(".") == 3:
            rec["ip"] = parts[3]
    return out


_AIX_NETMASK = {
    "0xff000000": 8, "0xffff0000": 16, "0xffffff00": 24, "0xfffffe00": 23,
    "0xfffffc00": 22, "0xfffff800": 21, "0xfffff000": 20, "0xffffff80": 25,
    "0xffffffc0": 26, "0xffffffe0": 27, "0xfffffff0": 28, "0xfffffff8": 29,
    "0xfffffffc": 30,
}


def parse_aix_ifconfig(text: str) -> dict[str, dict]:
    """`ifconfig -a` -> {介面: {ip, subnet, state}}。AIX 的 netmask 是 16 進位。"""
    import re as _re

    out: dict[str, dict] = {}
    cur = None
    for ln in (text or "").splitlines():
        m = _re.match(r"^(\w+):\s*flags=", ln)
        if m:
            cur = m.group(1)
            up = "UP" in ln.split("<", 1)[-1].split(">", 1)[0].split(",")
            out[cur] = {"state": "up" if up else "down"}
            continue
        if cur is None:
            continue
        m = _re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)\s+netmask\s+(0x[0-9a-f]{8})", ln, _re.I)
        if m:
            out[cur]["ip"] = m.group(1)
            bits = _AIX_NETMASK.get(m.group(2).lower())
            if bits:
                out[cur]["subnet"] = f"/{bits}"
    return out


def parse_aix_default_gw(text: str) -> str | None:
    """`netstat -rn` 的 default 那列第二欄就是預設閘道。"""
    for ln in (text or "").splitlines():
        parts = ln.split()
        if len(parts) >= 2 and parts[0] == "default":
            return parts[1]
    return None


def _aix_media_speed(v: str) -> int | None:
    """`media_speed` 的值 -> Mb/s。`Auto_Negotiation` 是**不知道**，不是 0。

    `1000_Full_Duplex` -> 1000、`10000_Full_Duplex` -> 10000。
    回 None 代表「這張卡設自動協商，額定速率要靠 entstat（需 root）才知道」。
    """
    t = (v or "").strip()
    head = t.split("_", 1)[0]
    return int(head) if head.isdigit() else None


def parse_aix_entinfo(text: str) -> dict[str, dict]:
    """每張 ent 卡的 `lsattr -El` ＋ `lscfg -vl` -> {卡名: {speed_max, model}}。"""
    import re as _re

    out: dict[str, dict] = {}
    cur = None
    for ln in (text or "").splitlines():
        if ln.startswith("@@ENT "):
            cur = ln[6:].strip()
            out.setdefault(cur, {})
            continue
        if cur is None:
            continue
        parts = ln.split()
        if len(parts) >= 2 and parts[0] == "media_speed":
            sp = _aix_media_speed(parts[1])
            if sp:
                out[cur]["speed_max"] = sp
        m = _re.search(r"^\s*\S+\s+(?:U\S+\s+)?(.+?Adapter.*)$", ln)
        if m and "model" not in out[cur]:
            out[cur]["model"] = m.group(1).strip()
    return out


def parse_aix_fcsinfo(text: str) -> dict[str, dict]:
    """每張 FC 卡的 `lscfg -vpl` ＋ `lsattr -El` -> {卡名: {wwpn, wwnn, model}}。

    `Network Address.............10000000C9AB1234` 就是 WWPN。
    """
    import re as _re

    out: dict[str, dict] = {}
    cur = None
    for ln in (text or "").splitlines():
        if ln.startswith("@@FCS "):
            cur = ln[6:].strip()
            out.setdefault(cur, {})
            continue
        if cur is None:
            continue
        m = _re.search(r"Network Address\.+([0-9A-Fa-f]{16})", ln)
        if m:
            out[cur]["wwpn"] = _fmt_wwn(m.group(1))
        m = _re.search(r"Device Specific\.\(Z8\)\.+([0-9A-Fa-f]{16})", ln)
        if m:      # Z8 是 WWNN（node name）
            out[cur]["wwnn"] = _fmt_wwn(m.group(1))
        m = _re.search(r"^\s*\S+\s+(?:U\S+\s+)?(.*Fibre Channel.*)$", ln)
        if m and "model" not in out[cur]:
            out[cur]["model"] = m.group(1).strip()
    return out


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
    notes: dict = {}
    if platform == "aix":
        for k, cmd in CMD_AIX.items():
            raw[k] = runner(host, cmd)
        nics, hbas, notes = _collect_aix_net(raw)
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
    return {"nics": nics, "hbas": hbas, "raw": raw, "notes": notes}


def _collect_aix_net(raw: dict) -> tuple[list[dict], list[dict], dict]:
    """AIX 的 NIC／HBA，外加**「為什麼是空的」**。

    使用者 2026-09-22 的規矩：收不到要講原因，不可以只顯示 0 或留白。
    這裡把三種情況分開，因為對看的人來說要做的事完全不同：

    ======================  ====================================================
    `lsdev` 列不出任何卡     連線／指令有問題（AIX 不可能沒有介面卡）
    `lsdev` 列得出但沒明細   權限不足（ODM 查詢被擋，或 entstat 需 root）
    `lsdev` 列得出卻沒 fcs   **這台真的沒有 FC HBA** -- 這一句才可以講「沒有」
    ======================  ====================================================
    """
    devs = parse_aix_lsdev(raw.get("lsdev", ""))
    netst = parse_aix_netstat_in(raw.get("netstat", ""))
    ifc = parse_aix_ifconfig(raw.get("ifconfig", ""))
    gw = parse_aix_default_gw(raw.get("route", ""))
    entinfo = parse_aix_entinfo(raw.get("entinfo", ""))
    fcsinfo = parse_aix_fcsinfo(raw.get("fcsinfo", ""))
    # 加分項：目前協商速率（多半要 root，拿不到就沒有）
    entstat = {n["nic_name"]: n for n in parse_aix_ent(raw.get("entstat", ""))}
    fcstat = {h["hba_name"]: h for h in parse_aix_fcs(raw.get("fcstat", ""))}

    notes: dict = {}
    lsdev_ok = bool(devs["ent"] or devs["fcs"])
    if not lsdev_ok:
        notes["nics"] = notes["hbas"] = (
            "`lsdev -Cc adapter` 沒有回任何介面卡。AIX 不可能沒有介面卡，"
            "所以這是連線或指令被擋，**不是這台沒有網卡／HBA**。")
        return [], [], notes

    nics: list[dict] = []
    for ent in devs["ent"]:
        # ent0 是卡，en0 是介面。IP 掛在介面上，卡的設定值在卡上，兩邊要併起來。
        en = "en" + ent[3:]
        info = entinfo.get(ent, {})
        st = entstat.get(ent, {})
        i_net = netst.get(en, {})
        i_cfg = ifc.get(en, {})
        nics.append({
            "nic_name": ent,
            "mac": i_net.get("mac") or st.get("mac"),
            "state": i_cfg.get("state"),
            "ip": i_cfg.get("ip") or i_net.get("ip"),
            "subnet": i_cfg.get("subnet"),
            "gateway": gw if (i_cfg.get("ip") or i_net.get("ip")) else None,
            "vlan": None, "bond": None,
            # 目前速率只有 entstat 給得出來（需 root）。沒有就是沒有，不拿額定值頂替 --
            # 那會讓「10G 卡實際跑 1G」這種最該被看到的事永遠不會亮燈。
            "speed_cur": st.get("speed_cur"),
            "speed_max": info.get("speed_max") or st.get("speed_max"),
            "driver": None, "model": info.get("model"),
        })
    if not entstat:
        notes["nic_speed"] = (
            "目前協商速率沒收到：AIX 要 `entstat -d` 才問得到，而它需要 root，"
            "收集帳號是唯讀非 root。額定速率（`lsattr` 的 media_speed）有收到的照列。"
            "**沒有目前速率 = 沒辦法判斷有沒有降速**，不是沒有降速。")

    hbas: list[dict] = []
    if not devs["fcs"]:
        # 這一句是唯一可以講「沒有」的情況，而且要講清楚依據
        notes["hbas"] = ("這台**沒有 FC HBA**：`lsdev -Cc adapter` 列得出介面卡"
                         f"（{len(devs['ent'])} 張乙太卡），但裡面沒有 fcs*／vfchost*。")
    for fcs in devs["fcs"]:
        info = fcsinfo.get(fcs, {})
        st = fcstat.get(fcs, {})
        hbas.append({
            "hba_name": fcs,
            "wwpn": info.get("wwpn"),
            "wwnn": info.get("wwnn"),
            "speed_cur": st.get("speed_cur"),
            "speed_max": None,
            "port_state": st.get("port_state"),
            "model": info.get("model"),
        })
    missing_wwpn = [h["hba_name"] for h in hbas if not h["wwpn"]]
    if missing_wwpn:
        notes["hba_wwpn"] = (
            f"有 {len(missing_wwpn)} 張 FC 卡查得到卡名卻拿不到 WWPN"
            f"（{', '.join(missing_wwpn)}）：`lscfg -vpl` 沒吐 Network Address，"
            "多半是權限。**沒有 WWPN 就沒辦法跟 SAN 交換器的 zone 對起來。**")
    return nics, hbas, notes


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
