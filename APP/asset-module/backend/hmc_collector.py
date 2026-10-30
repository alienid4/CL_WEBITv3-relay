"""HMC 收集器（IBM Power／AIX／IBM i 的管理主控台）。

安全模式跟 SAN／主機納管一致：SSH 走 onboard_engine._ssh_exec（密碼只在記憶體、不落地、
不寫 log；known_hosts accept-new），**只跑唯讀查詢指令、不下任何 ch* 變更指令**＝不踩 SOC。
A 版：帳密當場帶入、收完即丟、**不儲存憑證**，只落結果。

⚠️ 2026-09-13：parser（hmc_parse）依官方格式撰寫、尚未用實機驗過（環境連不到 HMC）。
"""
from __future__ import annotations

import json
import re

# 唯讀指令（{sys} 會換成從 HMC 自己查到的系統名，並先過濾成安全字元）。
# 只有 lssyscfg / lshwres / lshmc 這幾個「查詢」動詞；ch*（變更）一律不在名單。
CMD_SYS = "lssyscfg -r sys -F name:type_model:serial_num:state:curr_sys_firmware"
CMD_SYS_FULL = "lssyscfg -r sys"
CMD_HMC_VER = "lshmc -V"
_CMD_LPAR = "lssyscfg -r lpar -m {sys}"
_CMD_PROC = "lshwres -r proc -m {sys} --level lpar"
_CMD_MEM = "lshwres -r mem -m {sys} --level lpar"

_SAFE = re.compile(r'^[\w .+-]{1,80}$')   # 系統名允許的字元；擋掉可能的指令注入


def collect(ip: str, username: str, password: str, timeout: int = 40) -> dict:
    """連上 HMC，跑唯讀指令，回 hmc_parse.build_inventory() 的結構。密碼只在記憶體。"""
    import onboard_engine
    import hmc_parse

    def run(cmd: str) -> str:
        rc, out = onboard_engine._ssh_exec(ip, username, password, cmd, timeout=timeout)
        if rc == onboard_engine.SSH_CONNECT_FAILED:
            raise RuntimeError(out.strip() or f"無法連線到 {ip}")
        return out

    ver_out = run(CMD_HMC_VER)
    ver = None
    m = re.search(r'"?version=\s*([^\n",]+)', ver_out) or re.search(r'Version:\s*(\S+)', ver_out)
    if m:
        ver = m.group(1).strip()

    sys_text = run(CMD_SYS_FULL)
    lpar_by_sys, proc_by_sys, mem_by_sys = {}, {}, {}
    for line in hmc_parse.parse_kv_lines(sys_text):
        name = line.get('name')
        if not name or not _SAFE.match(name):
            continue
        lpar_by_sys[name] = run(_CMD_LPAR.format(sys=name))
        proc_by_sys[name] = run(_CMD_PROC.format(sys=name))
        mem_by_sys[name] = run(_CMD_MEM.format(sys=name))

    inv = hmc_parse.build_inventory(sys_text, lpar_by_sys, proc_by_sys, mem_by_sys, hmc_version=ver)
    inv["_raw"] = {"sys": sys_text}   # 只回這次、不落庫
    return inv


def store(conn, ip: str, inv: dict, by: str | None) -> dict:
    payload = {k: v for k, v in inv.items() if k != "_raw"}
    summary = {"ip": ip, "hmc_version": inv.get("hmc_version"),
               "system_count": inv.get("system_count", 0), "lpar_count": inv.get("lpar_count", 0)}
    conn.execute(
        "INSERT INTO hmc_console (ip, hmc_version, system_count, lpar_count, data_json, "
        "collected_at, collected_by) VALUES (?,?,?,?,?, datetime('now','localtime'), ?) "
        "ON CONFLICT(ip) DO UPDATE SET hmc_version=excluded.hmc_version, "
        "system_count=excluded.system_count, lpar_count=excluded.lpar_count, "
        "data_json=excluded.data_json, collected_at=datetime('now','localtime'), "
        "collected_by=excluded.collected_by",
        (ip, summary["hmc_version"], summary["system_count"], summary["lpar_count"],
         json.dumps(payload, ensure_ascii=False), by),
    )
    conn.commit()
    return summary


_HMC_HINTS = ("hmc", "hardware management console")


def list_consoles(conn) -> list[dict]:
    """列資產庫裡看起來是 HMC 的機器，附上次收集狀態。HMC 常沒登記，頁面也可手動輸入 IP 收。"""
    rows = conn.execute(
        "SELECT h.hostname, h.ip, h.device_model, h.asset_name, h.physical_location, h.environment, "
        "c.collected_at, c.system_count, c.lpar_count, c.hmc_version "
        "FROM hardware h LEFT JOIN hmc_console c ON c.ip = h.ip "
        "WHERE h.ip IS NOT NULL AND h.ip != ''"
    ).fetchall()
    known = {}
    for r in rows:
        blob = " ".join(str(r[k] or "") for k in ("device_model", "asset_name", "hostname")).lower()
        if any(h in blob for h in _HMC_HINTS):
            d = dict(r); d["collected"] = bool(d.get("collected_at")); known[d["ip"]] = d
    # 加上「有收過、但資產庫沒登記」的 HMC（手動輸入 IP 收過的）
    for r in conn.execute("SELECT * FROM hmc_console"):
        d = dict(r)
        if d["ip"] not in known:
            d["collected"] = True
            d["hostname"] = d.get("hostname") or ""
            known[d["ip"]] = d
    return list(known.values())
