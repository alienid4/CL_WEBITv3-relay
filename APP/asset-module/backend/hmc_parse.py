"""HMC（IBM Hardware Management Console）唯讀輸出 parser。

HMC CLI（lssyscfg／lshwres）預設輸出是每列一筆、`attr=value,attr=value` 逗號分隔；
值若含逗號會用雙引號包起來、內部雙引號用兩個表示。這支負責把那種格式解析成結構。

⚠️ 2026-09-13：此 parser 依 HMC 官方指令格式撰寫，**尚未用實機輸出驗證**（使用者當前
環境連不到 HMC）。第一次對實機收集時可能要依實際輸出微調。只解析文字、不連任何設備。
"""
from __future__ import annotations


def parse_kv_line(line: str) -> dict:
    """`a=1,b="x,y",c=3` → {'a':'1','b':'x,y','c':'3'}（尊重雙引號內的逗號）。"""
    out: dict[str, str] = {}
    i, n = 0, len(line)
    while i < n:
        # key
        eq = line.find('=', i)
        if eq < 0:
            break
        key = line[i:eq].strip()
        j = eq + 1
        # value（可能有引號）
        if j < n and line[j] == '"':
            buf = []
            j += 1
            while j < n:
                if line[j] == '"':
                    if j + 1 < n and line[j + 1] == '"':   # 兩個雙引號＝一個字面雙引號
                        buf.append('"'); j += 2; continue
                    j += 1; break
                buf.append(line[j]); j += 1
            val = ''.join(buf)
            if j < n and line[j] == ',':
                j += 1
        else:
            comma = line.find(',', j)
            if comma < 0:
                val = line[j:]; j = n
            else:
                val = line[j:comma]; j = comma + 1
        if key:
            out[key] = val.strip()
        i = j
    return out


def parse_kv_lines(text: str) -> list[dict]:
    rows = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or '=' not in ln:
            continue
        rows.append(parse_kv_line(ln))
    return rows


def build_inventory(sys_text: str, lpar_by_sys: dict[str, str] | None = None,
                    proc_by_sys: dict[str, str] | None = None,
                    mem_by_sys: dict[str, str] | None = None,
                    hmc_version: str | None = None) -> dict:
    """組出 {hmc_version, systems:[{name,type_model,serial,state,lpars:[...]}]}。"""
    lpar_by_sys = lpar_by_sys or {}
    proc_by_sys = proc_by_sys or {}
    mem_by_sys = mem_by_sys or {}

    systems = []
    for s in parse_kv_lines(sys_text):
        name = s.get('name')
        if not name:
            continue
        # LPAR 基本資料
        lpars = {}
        for lp in parse_kv_lines(lpar_by_sys.get(name, '')):
            key = lp.get('lpar_id') or lp.get('name')
            lpars[key] = {
                'name': lp.get('name'),
                'lpar_id': lp.get('lpar_id'),
                'env': lp.get('lpar_env'),          # aixlinux / vioserver / os400
                'state': lp.get('state'),
                'os_version': lp.get('os_version'),
            }
        # CPU
        for pr in parse_kv_lines(proc_by_sys.get(name, '')):
            key = pr.get('lpar_id') or pr.get('lpar_name')
            if key in lpars:
                lpars[key]['proc_units'] = pr.get('curr_proc_units') or pr.get('curr_procs')
                lpars[key]['procs'] = pr.get('curr_procs')
        # 記憶體（MB）
        for me in parse_kv_lines(mem_by_sys.get(name, '')):
            key = me.get('lpar_id') or me.get('lpar_name')
            if key in lpars:
                lpars[key]['mem_mb'] = me.get('curr_mem')
        systems.append({
            'name': name,
            'type_model': s.get('type_model'),
            'serial': s.get('serial_num'),
            'state': s.get('state'),
            'firmware': s.get('curr_sys_firmware') or s.get('sys_firmware'),
            'lpars': list(lpars.values()),
        })
    lpar_total = sum(len(s['lpars']) for s in systems)
    return {'hmc_version': hmc_version, 'systems': systems,
            'system_count': len(systems), 'lpar_count': lpar_total}
