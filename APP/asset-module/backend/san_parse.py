"""Brocade FOS 唯讀輸出 parser。

輸入是 SAN switch 幾個唯讀指令的純文字輸出（switchshow / fabricshow /
nscamshow / cfgactvshow / alishow），輸出成可盤點的結構：每個 WWPN → 別名、
WWNN、登入在哪台 switch 哪個 port、屬於哪些 zone。

**這支只解析文字，不連任何設備、不碰網路。** 收集（SSH）在 san_collector.py。
2026-09-13 用實機（IBM 8969＝Brocade OEM，FOS v9）輸出格式驗過。
"""
from __future__ import annotations

import re

WWPN = re.compile(r'(?:[0-9a-fA-F]{2}:){7}[0-9a-fA-F]{2}')


def parse_switchshow(text: str) -> tuple[dict, list[dict]]:
    info: dict = {}
    ports: list[dict] = []
    in_tbl = False
    for ln in text.splitlines():
        if re.match(r'^\s*Index\s+Port\s+Address', ln):
            in_tbl = True
            continue
        if not in_tbl:
            m = re.match(r'^\s*([A-Za-z][\w ]*?):\s+(.*\S)\s*$', ln)
            if m:
                info[m.group(1).strip()] = m.group(2).strip()
            continue
        if re.match(r'^=+$', ln.strip()):
            continue
        m = re.match(r'^\s*(\d+)\s+(\d+)\s+(\w+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(FC|--)?\s*(.*)$', ln)
        if not m:
            continue
        tail = (m.group(8) or '').strip()
        wm = WWPN.search(tail)
        ports.append({
            'index': m.group(1), 'port': m.group(2), 'address': m.group(3),
            'speed': m.group(5), 'state': m.group(6),
            'wwpn': wm.group(0).lower() if wm else None,  # 直連單一裝置才有；NPIV 多個時 None
            'note': tail,
        })
    return info, ports


def parse_fabricshow(text: str) -> list[dict]:
    out = []
    for ln in text.splitlines():
        m = re.match(r'^\s*(\d+):\s+(\w+)\s+(' + WWPN.pattern + r')\s+(\S+)\s+(\S+)\s+>?"?([^"]*)"?', ln)
        if m:
            out.append({'domain': m.group(1), 'wwn': m.group(3).lower(),
                        'enet_ip': m.group(4), 'name': m.group(6).strip()})
    return out


def _zone_blocks(text: str) -> dict[str, list[str]]:
    """抓 'zone: NAME' 後面縮排的成員（WWPN 或別名；分號或換行分隔）。"""
    zones: dict[str, list[str]] = {}
    cur = None
    for ln in text.splitlines():
        m = re.match(r'^\s*zone:\s+(\S+)\s*(.*)$', ln)
        if m:
            cur = m.group(1)
            zones[cur] = []
            rest = m.group(2)
            if rest:
                zones[cur] += [x.strip().rstrip(';') for x in rest.split(';') if x.strip()]
            continue
        if re.match(r'^\s*(cfg|alias):', ln):
            cur = None
            continue
        if cur and ln.strip():
            zones[cur] += [x.strip().rstrip(';') for x in ln.split(';') if x.strip()]
    return zones


def parse_cfgactvshow(text: str) -> tuple[str | None, dict[str, list[str]]]:
    cfg = None
    mc = re.search(r'^\s*cfg:\s+(\S+)', text, re.M)
    if mc:
        cfg = mc.group(1)
    return cfg, _zone_blocks(text)


def parse_alishow(text: str) -> dict[str, list[str]]:
    """alias: NAME → [WWPN...]（別名通常就是主機/儲存埠的好記名）。"""
    aliases: dict[str, list[str]] = {}
    cur = None
    for ln in text.splitlines():
        m = re.match(r'^\s*alias:\s+(\S+)\s*(.*)$', ln)
        if m:
            cur = m.group(1)
            aliases[cur] = [w.lower() for w in WWPN.findall(m.group(2))]
            continue
        if re.match(r'^\s*(cfg|zone):', ln):
            cur = None
            continue
        if cur:
            aliases[cur] += [w.lower() for w in WWPN.findall(ln)]
    return aliases


def parse_zoneshow(text: str) -> dict:
    """`zoneshow` → **所有定義過的** zone 與 zone config（2026-10-07，搬遷重建）。

    ## 為什麼 `cfgactvshow` 不夠

    `cfgactvshow` 只給**目前啟用中**的那一份設定。`zoneshow` 給的是 switch 上
    **定義過的全部**——包含沒啟用的 zone 與備用的 config。

    搬遷時這個差別會咬人：常見做法是為新機房預先定義一份 config，搬完才 `cfgenable`。
    只收 active 的話，那份預先定義的東西在我們的資料裡**完全不存在**；
    反過來，搬完要是啟錯 config，也沒有東西可以對照回去。

    這份原文**早就收進 raw_json 了**（`READONLY_COMMANDS` 裡有 `zoneshow`），
    只是從來沒有解析器看過它。所以這一刀不需要重連任何 switch。

    回 `{"active_cfg": 名稱或 None, "configs": {cfg: [zone...]}, "zones": {zone: [成員...]},
         "inactive_zones": [沒被任何 config 收錄的 zone]}`

    ⚠️ `active_cfg` 是 None 時意思是「這份輸出裡沒講哪一個在用」，
    **不是「沒有啟用任何 config」**。要判「真的沒啟用」得看 `switchshow` 的 zoning 欄。

    ⚠️ **只採 `Defined configuration:` 段的 zone 成員**。`zoneshow` 會把啟用中的 zone
    在 `Effective configuration:` 段再印一次，而那一份是**解析後的結果**（別名已展開、
    只列生效的成員）。兩段混著讀的話後者會蓋掉前者，於是「定義」被「生效結果」取代——
    重建要照著做的是定義，不是結果。
    """
    # 兩段切開。沒有 Effective 標頭的版本（或只跑 `zoneshow --validate`）就整份都算定義段。
    parts = re.split(r'^\s*Effective configuration:\s*$', text or "", maxsplit=1, flags=re.M | re.I)
    defined, effective = parts[0], (parts[1] if len(parts) > 1 else "")

    zones = _zone_blocks(defined)
    configs: dict[str, list[str]] = {}
    cur_cfg = None
    for ln in defined.splitlines():
        m = re.match(r'^\s*cfg:\s+(\S+)\s*(.*)$', ln)
        if m:
            cur_cfg = m.group(1)
            configs.setdefault(cur_cfg, [])
            rest = m.group(2)
            if rest:
                configs[cur_cfg] += [x.strip().rstrip(';') for x in rest.split(';') if x.strip()]
            continue
        if re.match(r'^\s*(zone|alias):', ln):
            cur_cfg = None
            continue
        if cur_cfg and ln.strip():
            configs[cur_cfg] += [x.strip().rstrip(';') for x in ln.split(';') if x.strip()]

    ma = re.search(r'^\s*cfg:\s+(\S+)', effective, re.M)
    active = ma.group(1) if ma else None
    in_cfg = {z for members in configs.values() for z in members}
    return {"active_cfg": active, "configs": configs, "zones": zones,
            "inactive_zones": sorted(z for z in zones if z not in in_cfg)}


def parse_nsshow(text: str) -> dict[str, dict]:
    """`nsshow` → **本機 switch 這幾個埠上**接了什麼（2026-10-07，搬遷重建）。

    跟 `parse_nscamshow` 的差別很重要，不是重複：

    * `nscamshow` ＝ 全 fabric 的 name server 快取（**別台 switch 上的裝置也會列**）
    * `nsshow`    ＝ **只有這一台** switch 本地登錄的裝置，而且帶**埠號**

    重建要的是後者：**哪一條線插在哪個埠**。沒有埠號，搬完插錯埠，
    zoning 是照 WWPN 走所以還是會通，但 ISL 流量路徑與埠速率全跑掉，
    而且現場沒有東西可以對照「原本插哪」。

    典型輸出（FOS）::

         N    011000;      2,3;20:00:00:25:b5:11:00:0a;20:00:00:25:b5:11:00:0b; na
            FC4s: FCP
            PortSymb: [35] "Linux-host hba0"

    `Port Index` 那個 `2,3` 的第一個數字是埠號。格式各版略有差異，
    **抓不到埠號就留 None，不要拿別的欄位硬湊**。
    """
    devs: dict[str, dict] = {}
    blocks = re.split(r'\n(?=\s*N[L ]?\s+\w+;)', text)
    for b in blocks:
        ws = [w.lower() for w in WWPN.findall(b)]
        if not ws:
            continue
        port = ws[0]
        head = re.search(r'^\s*(N[L ]?)\s+([0-9a-fA-F]+);\s*([0-9]+)\s*,\s*([0-9]+)\s*;', b, re.M)
        sym = re.search(r'(?:PortSymb|NodeSymb)[^:]*:\s*(?:\[\d+\]\s*)?"?([^"\n]+)"?', b)
        fc4 = re.search(r'FC4s:\s*(.+)', b)
        devs[port] = {
            "wwnn": ws[1] if len(ws) > 1 else None,
            "port_id": head.group(2) if head else None,
            # 這是「插在第幾個埠」——重建時要照著插回去的那個數字
            "port_index": head.group(4) if head else None,
            "device_type": head.group(1).strip() if head else None,
            "symbol": sym.group(1).strip() if sym else None,
            "fc4": fc4.group(1).strip() if fc4 else None,
        }
    return devs


def parse_nscamshow(text: str) -> dict[str, dict]:
    """name server（全 fabric）：抓 WWPN 附近的 WWNN／type／symbolic。
    格式各版略有差異，這裡保守只抓「每筆有 Port WWPN 就建一筆，順帶抓同段的 Node WWPN」。"""
    devs: dict[str, dict] = {}
    blocks = re.split(r'\n(?=\s*(?:N |NL|Type:))', text)
    for b in blocks:
        ws = [w.lower() for w in WWPN.findall(b)]
        if not ws:
            continue
        port = ws[0]
        node = ws[1] if len(ws) > 1 else None
        sym = re.search(r'(?:PortSymb|NodeSymb|Symb)[^:]*:\s*(.+)', b)
        devs[port] = {'wwnn': node, 'symbol': sym.group(1).strip() if sym else None}
    return devs


def build_inventory(switchshow='', fabricshow='', cfgactvshow='', alishow='', nscamshow='') -> dict:
    info, ports = parse_switchshow(switchshow)
    fabric = parse_fabricshow(fabricshow)
    cfg, active_zones = parse_cfgactvshow(cfgactvshow)
    aliases = parse_alishow(alishow)
    ns = parse_nscamshow(nscamshow)

    sw_name = info.get('switchName') or (fabric[0]['name'] if fabric else '?')
    sw_wwn = info.get('switchWwn') or (fabric[0]['wwn'] if fabric else None)
    enet_ip = fabric[0]['enet_ip'] if fabric else None

    wwpn2alias: dict[str, str] = {}
    for a, ws in aliases.items():
        for w in ws:
            wwpn2alias.setdefault(w, a)
    wwpn2port = {p['wwpn']: p for p in ports if p['wwpn']}
    wwpn2zones: dict[str, list[str]] = {}
    for z, members in active_zones.items():
        for mem in members:
            if WWPN.fullmatch(mem):
                wwpn2zones.setdefault(mem.lower(), []).append(z)

    all_wwpns = set(wwpn2alias) | set(wwpn2port) | set(wwpn2zones) | set(ns)
    rows = []
    for w in sorted(all_wwpns):
        p = wwpn2port.get(w)
        rows.append({
            'wwpn': w,
            'wwnn': (ns.get(w) or {}).get('wwnn'),
            'alias': wwpn2alias.get(w, ''),
            'switch': sw_name if p else '',
            'port': p['port'] if p else '',
            'zones': wwpn2zones.get(w, []),
        })
    return {
        'switch': sw_name, 'switch_wwn': sw_wwn, 'enet_ip': enet_ip,
        'zoning_cfg': cfg, 'fabric': fabric, 'rows': rows,
    }


# ===== 其餘指令的解析（2026-09-15）=====
# 使用者：「.158 的 PuTTY 紀錄，你要紀錄到資產查詢——應該說身家調查表」。
# 原本只解析 WWPN／zone 五個指令，其他九個只存原文。格式照真實 FOS v9.1（IBM 8969）輸出寫，
# 測試用合成資料。每支都「認不出來就回空」，不丟例外——舊版 FOS 格式不同時，
# 其他欄位照樣收得到，原文也另有存檔可以日後重解。


def _kv(text: str) -> dict:
    """「Key:   value」這種行 → dict（key 去空白）。"""
    out = {}
    for ln in (text or "").splitlines():
        m = re.match(r'^\s*([A-Za-z][\w /().-]*?):\s*(.*\S)\s*$', ln)
        if m:
            out.setdefault(m.group(1).strip(), m.group(2).strip())
    return out


def _one(v):
    """把 FOS 為了對齊塞的多個空白壓成一個（「45      Centigrade」→「45 Centigrade」）。"""
    return " ".join(str(v).split()) if v else v


def parse_version(text: str) -> dict:
    kv = _kv(text)
    return {k: kv[src] for k, src in (("fos", "Fabric OS"), ("kernel", "Kernel"),
                                       ("bootprom", "BootProm"), ("made_on", "Made on"),
                                       ("flash", "Flash")) if src in kv}


def parse_firmwareshow(text: str) -> dict:
    """FOS primary／secondary 版本（兩個不一樣＝升級到一半或切換過，要注意）。"""
    vers = re.findall(r'\bv\d+\.\d+[\w.]*', text or "")
    if not vers:
        return {}
    return {"primary": vers[0], "secondary": vers[1] if len(vers) > 1 else None,
            "consistent": len(set(vers[:2])) == 1}


def parse_chassisshow(text: str) -> dict:
    """機箱序號／料號／製造日期＋電源與風扇。以「XXX Unit: n」切段。"""
    blocks: list[tuple[str, dict]] = []
    cur_name, cur_lines = None, []
    for ln in (text or "").splitlines():
        m = re.match(r'^\s*([A-Z][A-Z /]*?)\s+Unit:\s*(\d+)', ln)
        if m:
            if cur_name:
                blocks.append((cur_name, _kv("\n".join(cur_lines))))
            cur_name, cur_lines = f"{m.group(1).strip()}#{m.group(2)}", []
        elif cur_name:
            cur_lines.append(ln)
    if cur_name:
        blocks.append((cur_name, _kv("\n".join(cur_lines))))

    out: dict = {"power_supplies": [], "fans": []}
    top = _kv((text or "").split("Unit:")[0]) if "Unit:" in (text or "") else _kv(text)
    if "Chassis State" in top:
        out["chassis_state"] = top["Chassis State"]
    for name, kv in blocks:
        kind = name.split("#")[0]
        if kind.startswith("CHASSIS"):
            out.update({k: kv[src] for k, src in (
                ("serial", "Serial Num"), ("part_num", "Part Num"),
                ("factory_serial", "Factory Serial Num"), ("factory_part_num", "Factory Part Num"),
                ("id", "ID"), ("airflow", "System AirFlow"), ("time_alive", "Time Alive"))
                if src in kv})
            if "Manufacture" in kv:
                m = re.search(r'Day:\s*(\d+)\s+Month:\s*(\d+)\s+Year:\s*(\d+)', kv["Manufacture"])
                if m:
                    y = int(m.group(3))
                    y = y + 2000 if y < 100 else y
                    out["manufactured"] = f"{y:04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
        elif kind.startswith("POWER"):
            out["power_supplies"].append({"unit": name.split("#")[1], "source": kv.get("Power Source"),
                                          "usage": kv.get("Power Usage"), "awake": kv.get("Time Awake")})
        elif kind.startswith("FAN"):
            out["fans"].append({"unit": name.split("#")[1], "direction": kv.get("Fan Direction")})
    return out


def port_summary(ports: list[dict]) -> dict:
    """switchshow 的 port 表 → 幾個 port、幾個在線、各速度幾個。"""
    from collections import Counter

    states = Counter((p.get("state") or "").strip() for p in ports)
    speeds = Counter((p.get("speed") or "").strip() for p in ports)
    return {"total": len(ports), "online": states.get("Online", 0),
            "no_light": states.get("No_Light", 0), "by_state": dict(states), "by_speed": dict(speeds)}


#: porterrshow 欄位順序（FOS 表頭：frames tx/rx、enc in、crc err、crc g_eof、too shrt、too long、
#: bad eof、enc out、disc c3、link fail、loss sync、loss sig、frjt、fbsy、c3timeout tx/rx、pcs err、uncor err）
PORTERR_COLS = ["frames_tx", "frames_rx", "enc_in", "crc_err", "crc_g_eof", "too_shrt", "too_long",
                "bad_eof", "enc_out", "disc_c3", "link_fail", "loss_sync", "loss_sig", "frjt", "fbsy",
                "c3timeout_tx", "c3timeout_rx", "pcs_err", "uncor_err"]
#: 真的代表線路／光模組有問題的計數。2026-09-15 真實資料：在線 port 幾乎都有 disc_c3、loss_sig
#: （丟框、重開機時失去光訊號，常見而且通常無害）——全部當錯誤會讓 4 個在線 port 都亮紅，
#: 真正要看的那個（link fail 124）反而被淹掉。
PORTERR_SERIOUS = {"enc_in", "crc_err", "crc_g_eof", "too_shrt", "too_long", "bad_eof", "enc_out",
                   "link_fail", "loss_sync", "pcs_err", "uncor_err"}


def parse_porterrshow(text: str) -> dict:
    """每個 port 的錯誤計數，分成「真的有問題」（CRC／編碼／link fail…）跟「其他非 0」（丟框、失光…）。"""
    rows = []
    for ln in (text or "").splitlines():
        m = re.match(r'^\s*(\d+):\s+(.*)$', ln)
        if not m:
            continue
        cols = m.group(2).split()
        if len(cols) < 3:
            continue
        vals = dict(zip(PORTERR_COLS, cols))
        nonzero = {k: v for k, v in vals.items()
                   if not k.startswith("frames") and v not in ("0", "0.0")}
        serious = {k: v for k, v in nonzero.items() if k in PORTERR_SERIOUS}
        rows.append({"port": m.group(1), "tx": vals.get("frames_tx"), "rx": vals.get("frames_rx"),
                     "nonzero": nonzero, "serious": serious, "has_errors": bool(serious)})
    return {
        "ports": len(rows),
        "ports_with_errors": [r["port"] for r in rows if r["has_errors"]],
        "errors_detail": {r["port"]: r["serious"] for r in rows if r["has_errors"]},
        "ports_with_other_counters": [r["port"] for r in rows if r["nonzero"] and not r["has_errors"]],
        "rows": rows,
    }


def parse_sfpshow(text: str) -> list[dict]:
    """每個 port 的光模組：廠牌、料號、序號、速率、溫度、收發功率。沒插的 port 不列。"""
    out = []
    parts = re.split(r'^\s*Port\s+(\d+):\s*$', text or "", flags=re.M)
    # parts = [前言, port, 內容, port, 內容, ...]
    for i in range(1, len(parts) - 1, 2):
        kv = _kv(parts[i + 1])
        if not kv.get("Vendor Name") and not kv.get("Serial No"):
            continue
        out.append({
            "port": parts[i],
            "vendor": kv.get("Vendor Name"), "part_num": kv.get("Vendor PN"),
            "serial": kv.get("Serial No"), "transceiver": kv.get("Transceiver"),
            "wavelength": _one(kv.get("Wavelength")), "temperature": _one(kv.get("Temperature")),
            "rx_power": _one(kv.get("RX Power")), "tx_power": _one(kv.get("TX Power")),
            "power_on": kv.get("Pwr On Time"),
        })
    return out


def parse_islshow(text: str) -> list[dict]:
    """switch 之間的串接（ISL）。「No ISL found」回空清單。"""
    out = []
    for ln in (text or "").splitlines():
        m = re.match(r'^\s*(\d+):\s*(\d+)->\s*(\d+)\s+(' + WWPN.pattern + r')\s+(\d+)\s+(\S+)', ln)
        if m:
            out.append({"local_port": m.group(2), "remote_port": m.group(3),
                        "remote_wwn": m.group(4).lower(), "remote_domain": m.group(5),
                        "remote_name": m.group(6)})
    return out


def local_mgmt_ip(fabricshow: str, switch_name: str | None = None) -> str | None:
    """這份畫面是哪一台 switch 的管理 IP。

    fabricshow 在有串接（ISL）的 fabric 會列出**所有** switch——不能直接取第一列。
    FOS 會在「自己這台」的名稱前面加 `>`；沒有記號時，只有一台就是它，
    有多台就用 switchshow 的 switchName 對。都對不上回 None（無法判斷，不猜）。
    """
    entries = []
    for ln in (fabricshow or "").splitlines():
        m = re.match(r'^\s*(\d+):\s+(\w+)\s+(' + WWPN.pattern + r')\s+(\S+)\s+(\S+)\s+(>?)"?([^"]*)"?', ln)
        if m:
            entries.append({"ip": m.group(4), "local": m.group(6) == ">", "name": m.group(7).strip()})
    marked = [e for e in entries if e["local"]]
    if len(marked) == 1:
        return marked[0]["ip"]
    if len(entries) == 1:
        return entries[0]["ip"]
    if switch_name:
        named = [e for e in entries if e["name"] == switch_name]
        if len(named) == 1:
            return named[0]["ip"]
    return None


def build_details(outputs: dict) -> dict:
    """14 個指令裡「身家調查表」要的東西。缺哪個指令就少哪一塊，不丟例外。"""
    info, ports = parse_switchshow(outputs.get("switchshow", ""))
    fabric = parse_fabricshow(outputs.get("fabricshow", ""))
    return {
        "switch_type": info.get("switchType"), "switch_state": info.get("switchState"),
        "switch_role": info.get("switchRole"), "domain": info.get("switchDomain"),
        "zoning": info.get("zoning"),
        # 有串接的 fabric 會列多台，取「自己這台」（2026-09-15 IP 防呆時一併修正）
        "mgmt_ip": local_mgmt_ip(outputs.get("fabricshow", ""), info.get("switchName")),
        "version": parse_version(outputs.get("version", "")),
        "firmware": parse_firmwareshow(outputs.get("firmwareshow", "")),
        "chassis": parse_chassisshow(outputs.get("chassisshow", "")),
        "ports": port_summary(ports),
        "port_errors": parse_porterrshow(outputs.get("porterrshow", "")),
        "sfps": parse_sfpshow(outputs.get("sfpshow", "")),
        "isls": parse_islshow(outputs.get("islshow", "")),
        # ---- 2026-10-07 搬遷重建：這兩份原文一直都收進 raw_json，但沒有解析器看過 ----
        # zoning_all 是「定義過的全部」（cfgactvshow 只有啟用中那份）；
        # local_devices 帶**埠號**（nscamshow 是全 fabric、沒有埠號）。
        "zoning_all": parse_zoneshow(outputs.get("zoneshow", "")),
        "local_devices": parse_nsshow(outputs.get("nsshow", "")),
    }
