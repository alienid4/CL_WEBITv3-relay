"""SAN switch（Brocade）收集器。

安全模式（跟主機納管同一套，見 onboard_engine）：
- SSH 走程式內 paramiko（`onboard_engine._ssh_exec`）：密碼只在記憶體、不進 argv、不落磁碟、
  不寫 log；主機金鑰 accept-new 寫 known_hosts。
- **只跑唯讀白名單指令**（下面 READONLY_COMMANDS），其他一律不執行——不接受呼叫端傳任意指令。
- 全程不下任何寫入/設定指令 → 不動 switch 設定 → 不踩 SOC。
- **A 版模式**：帳密由呼叫端當場帶進來、收完即丟；本模組**不儲存任何憑證**。
  只把「解析後的結果」交給 store() 落庫（san_switch 表），供搬遷盤點與統計用。
2026-09-13 建立；parser（san_parse）已用實機 Brocade FOS v9 輸出驗過格式。
"""
from __future__ import annotations

import json

# 唯讀白名單：只有這些「顯示」指令會被送到 switch，呼叫端不能塞別的。
# 2026-09-13 使用者：一次收齊、多收一點，避免以後資訊不足又要回去重收（尤其防火牆/現場
# 一次性存取）。除了盤點主力，多帶硬體/序號/PSU/光模組/韌體/完整zoning/健康等唯讀資訊。
# ⚠️ 全部是查詢指令，沒有任何 ch*/寫入。原始輸出會一併存起來（raw_json），日後想要當初
# 沒解析的欄位，直接重新判讀舊原始檔即可，不必再連 switch。
#: 2026-09-15 使用者定的標準清單與順序：「你要放在離線版，讓他去 COPY 出來的格式才會統一」。
#: 離線匯入畫面照這份列出＋一鍵複製，線上收集也跑同一份——只有一份，兩邊不會各說各話。
READONLY_COMMANDS = [
    "switchname", "version", "switchshow", "fabricshow", "nscamshow", "nsshow", "cfgactvshow",
    "zoneshow", "alishow", "chassisshow", "firmwareshow", "islshow", "porterrshow", "sfpshow -all",
]
#: 判讀 WWPN／zone 至少要有這幾個（缺了匯入照做，但畫面要講缺哪個）
MIN_REQUIRED = ("switchshow", "fabricshow", "nscamshow", "cfgactvshow", "alishow")


def offline_guide() -> dict:
    """離線匯入畫面用：步驟＋標準指令清單。步驟裡的 <IP> 由人自己換。"""
    return {
        "commands": list(READONLY_COMMANDS),
        "min_required": list(MIN_REQUIRED),
        "steps": [
            "在收集主機開始錄畫面：script -q /tmp/san_<IP>.log",
            "登入 switch：telnet <IP>（或 ssh）",
            "一次貼一行指令，等出現提示字元（例：B24_left:admin>）再貼下一行；"
            "看到「Type <CR> to continue」就一直按 Enter 到輸出結束",
            "跑完輸入 exit 離開 switch，再 exit 結束錄畫面",
            "cat /tmp/san_<IP>.log，把整段（要含「提示字元＋指令」那一行）貼到下面",
            "匯入完刪掉紀錄檔：rm -f /tmp/san_<IP>.log",
        ],
    }
#: 指令的第一個字（給離線 transcript 切段、及輸出 key 用）
_BASES = [c.split()[0] for c in READONLY_COMMANDS]


def _parse_outputs(outputs: dict[str, str]):
    import san_parse
    inv = san_parse.build_inventory(
        switchshow=outputs.get("switchshow", ""),
        fabricshow=outputs.get("fabricshow", ""),
        cfgactvshow=outputs.get("cfgactvshow", ""),
        alishow=outputs.get("alishow", ""),
        nscamshow=outputs.get("nscamshow", ""),
    )
    # 其餘指令（韌體／序號／電源／port 狀態／錯誤／光模組／ISL）——身家調查表用（2026-09-15）
    try:
        inv["details"] = san_parse.build_details(outputs)
    except Exception as exc:  # noqa: BLE001 - 細節解析失敗不能讓 WWPN 那部分也跟著失敗
        inv["details"] = {"parse_error": f"{type(exc).__name__}: {exc}"}
    inv["_raw"] = outputs   # 原始輸出（會落庫 raw_json）
    return inv


def collect(ip: str, username: str, password: str, timeout: int = 40) -> dict:
    """連上 switch、跑唯讀白名單、解析成盤點結構。密碼只在記憶體、不落任何地方。
    輸出以「指令第一個字」為 key（switchshow/chassisshow…），原始輸出隨結果一起存。
    連線階段失敗直接丟 RuntimeError（訊息是 OpenSSH 風格、不含密碼）。
    """
    import onboard_engine

    outputs: dict[str, str] = {}
    for cmd in READONLY_COMMANDS:
        rc, out = onboard_engine._ssh_exec(ip, username, password, cmd, timeout=timeout)
        if rc == onboard_engine.SSH_CONNECT_FAILED:
            raise RuntimeError(out.strip() or f"無法連線到 {ip}")
        outputs[cmd.split()[0]] = out
    return _parse_outputs(outputs)


def split_transcript(text: str) -> dict[str, str]:
    """把一整段 SSH 畫面（PuTTY 存的 log）依指令回音切成 {指令: 輸出}。

    防火牆還沒開通、收集器連不到 switch 時的離線路徑：使用者自己在 switch 上跑唯讀
    指令、把整段畫面貼進來，這裡靠 `prompt> <cmd>` 這種回音行切段（只認白名單指令）。
    """
    import re

    lines = text.splitlines()
    marks: list[tuple[int, str]] = []
    for i, ln in enumerate(lines):
        # 指令回音行：prompt> cmd [args]；取第一個字，允許後面有參數（如 sfpshow -all）
        m = re.search(r'>\s*([A-Za-z]\w+)(?:\s+\S+)*\s*$', ln)
        if m and m.group(1) in _BASES:
            marks.append((i, m.group(1)))
    out: dict[str, str] = {}
    for k, (i, cmd) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(lines)
        out[cmd] = "\n".join(lines[i + 1:end])
    return out


def _norm_wwpn(w: str | None) -> str:
    """去冒號、小寫，兩邊格式才對得上。"""
    return (w or "").replace(":", "").replace("0x", "").strip().lower()


def reconcile_hba_san(conn, only_serial: str | None = None) -> dict:
    """用主機收到的 HBA WWPN，去已收的 SAN switch 資料反查 fabric/switch/port/zone/target。

    ＝把「主機端有哪些 HBA」跟「SAN 端這些 WWPN 掛在哪台 switch、哪個 zone」接起來，
    填進 asset_hba 的 SAN 衍生欄（來源標 san）。SAN 或主機任一邊更新後都呼叫一次。
    target_wwpn＝同 zone 內、非本機的其他 WWPN（單一發起端 zoning 下多半就是儲存端）。
    """
    # 建索引：WWPN → {switch, port, zones}；zone → 成員 WWPN 集合
    idx: dict[str, dict] = {}
    zone_members: dict[str, set] = {}
    for r in conn.execute("SELECT switch_name, data_json FROM san_switch").fetchall():
        try:
            data = json.loads(r["data_json"] or "{}")
        except (ValueError, TypeError):
            continue
        swname = data.get("switch") or r["switch_name"]
        for row in data.get("rows", []):
            w = _norm_wwpn(row.get("wwpn"))
            if not w:
                continue
            idx[w] = {"switch": row.get("switch") or swname, "port": row.get("port"),
                      "zones": row.get("zones") or []}
            for z in (row.get("zones") or []):
                zone_members.setdefault(z, set()).add(w)

    q = "SELECT id, asset_serial, wwpn, sources FROM asset_hba WHERE wwpn IS NOT NULL AND wwpn != ''"
    params: list = []
    if only_serial:
        q += " AND asset_serial = ?"
        params.append(only_serial)
    matched = 0
    for h in conn.execute(q, params).fetchall():
        hit = idx.get(_norm_wwpn(h["wwpn"]))
        if not hit:
            continue
        zones = hit["zones"]
        targets: set = set()
        for z in zones:
            targets |= zone_members.get(z, set())
        targets.discard(_norm_wwpn(h["wwpn"]))
        try:
            src = json.loads(h["sources"] or "{}")
        except (ValueError, TypeError):
            src = {}
        for k in ("san_switch", "san_port", "zone", "target_wwpn"):
            src[k] = "san"
        conn.execute(
            "UPDATE asset_hba SET san_switch=?, san_port=?, zone=?, target_wwpn=?, sources=? WHERE id=?",
            (hit["switch"], hit["port"], " / ".join(zones) or None,
             ", ".join(sorted(targets)) or None, json.dumps(src, ensure_ascii=False), h["id"]),
        )
        matched += 1
    conn.commit()
    return {"matched": matched}


def store(conn, ip: str, inv: dict, by: str | None) -> dict:
    """把解析結果落庫（一台 switch 一列）。**不存任何帳密。**"""
    rows = inv.get("rows", [])
    zones = {z for r in rows for z in (r.get("zones") or [])}
    port_count = sum(1 for r in rows if r.get("port"))
    summary = {
        "ip": ip,
        "switch_name": inv.get("switch"),
        "switch_wwn": inv.get("switch_wwn"),
        "zoning_cfg": inv.get("zoning_cfg"),
        "port_count": port_count,
        "zone_count": len(zones),
        "wwpn_count": len(rows),
    }
    payload = {k: v for k, v in inv.items() if k != "_raw"}
    raw_json = json.dumps(inv.get("_raw", {}), ensure_ascii=False)   # 原始輸出全存，日後可重判讀不必重連
    conn.execute(
        "INSERT INTO san_switch (ip, switch_name, switch_wwn, zoning_cfg, vendor, "
        "port_count, zone_count, wwpn_count, data_json, raw_json, collected_at, collected_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?, datetime('now','localtime'), ?) "
        "ON CONFLICT(ip) DO UPDATE SET switch_name=excluded.switch_name, "
        "switch_wwn=excluded.switch_wwn, zoning_cfg=excluded.zoning_cfg, "
        "vendor=excluded.vendor, port_count=excluded.port_count, "
        "zone_count=excluded.zone_count, wwpn_count=excluded.wwpn_count, "
        "data_json=excluded.data_json, raw_json=excluded.raw_json, "
        "collected_at=datetime('now','localtime'), collected_by=excluded.collected_by",
        (ip, summary["switch_name"], summary["switch_wwn"], summary["zoning_cfg"], "brocade",
         summary["port_count"], summary["zone_count"], summary["wwpn_count"],
         json.dumps(payload, ensure_ascii=False), raw_json, by),
    )
    # 身家調查表同步（2026-09-15 使用者）：switch 自己回報的機箱序號寫進資產的「設備序號」。
    # ⚠️ 只補空的——資產上已經有值、而且跟 switch 講的不一樣時**不覆蓋**，列成衝突給人看
    # （可能是登記錯、也可能是換過機箱；誰對要人判斷，機器不猜）。
    serial = ((inv.get("details") or {}).get("chassis") or {}).get("serial")
    summary["asset_serial_filled"] = []
    summary["asset_serial_conflicts"] = []
    if serial:
        for a in conn.execute("SELECT asset_serial, hw_serial FROM hardware WHERE ip = ?", (ip,)).fetchall():
            cur = (a["hw_serial"] or "").strip()
            if not cur:
                conn.execute("UPDATE hardware SET hw_serial = ? WHERE asset_serial = ?", (serial, a["asset_serial"]))
                summary["asset_serial_filled"].append(a["asset_serial"])
            elif cur != serial:
                summary["asset_serial_conflicts"].append(
                    {"asset_serial": a["asset_serial"], "asset_has": cur, "switch_says": serial})
    conn.commit()
    return summary


# 哪些資產算「SAN switch」（收集頁面要列的清單）。靠登記的名稱/型號/OS 關鍵字判斷。
_SAN_HINTS = ("san switch", "sansw", "brocade", "fos", "fc switch")


def list_switches(conn) -> list[dict]:
    """列出資產庫裡的 SAN switch，附上「上次收集」狀態（LEFT JOIN san_switch）。"""
    rows = conn.execute(
        "SELECT h.asset_serial, h.hostname, h.ip, h.device_model, h.asset_name, "
        "h.physical_location, h.environment, h.os, h.asset_status, "
        "s.collected_at, s.wwpn_count, s.zone_count, s.port_count, s.switch_name "
        "FROM hardware h LEFT JOIN san_switch s ON s.ip = h.ip "
        "WHERE h.ip IS NOT NULL AND h.ip != ''"
    ).fetchall()
    out = []
    for r in rows:
        blob = " ".join(str(r[k] or "") for k in ("device_model", "asset_name", "hostname", "os")).lower()
        if not any(h in blob for h in _SAN_HINTS):
            continue
        d = dict(r)
        d["collected"] = bool(d.get("collected_at"))
        out.append(d)
    return out


# ===== 原文存檔：每次離線匯入／線上收集的原始內容，只增不改（2026-09-15）=====
# 使用者：「離線匯入時的紀錄，你通通都要記錄，哪一天用得到，不知道。」
#
# san_switch 只留每台最新一次、而且只存「有認到的指令」的輸出。貼上的整段原文若有
# 沒認到的指令（格式差異、夾控制字元），那段就沒存到——之後修好解析也救不回來，
# 只能叫人再去 switch 收一次。所以原文**整段、每次**都另存一份，永遠不覆蓋。
# 不存帳密（離線匯入本來就沒有帳密；線上收集只存指令輸出）。

ARCHIVE_SQL = """
CREATE TABLE IF NOT EXISTS san_import_archive (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ip          TEXT NOT NULL,
    kind        TEXT NOT NULL,         -- offline_import / online_collect
    ok          INTEGER,               -- 這次有沒有成功入庫（失敗的原文一樣留）
    content     TEXT NOT NULL,         -- 貼上的整段原文，或線上收集的全部指令輸出
    recognized  TEXT,                  -- JSON：認到的指令
    missing     TEXT,                  -- JSON：標準清單裡缺的指令
    note        TEXT,
    created_at  TEXT NOT NULL,
    created_by  TEXT
)
"""


def archive(conn, ip: str, kind: str, content: str, ok: bool, recognized=None,
            missing=None, note: str | None = None, by: str | None = None) -> int:
    """存一份原文。回傳存檔編號。"""
    from datetime import datetime

    conn.execute(ARCHIVE_SQL)
    cur = conn.execute(
        "INSERT INTO san_import_archive (ip, kind, ok, content, recognized, missing, note, "
        "created_at, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
        (ip, kind, 1 if ok else 0, content or "",
         json.dumps(sorted(recognized or []), ensure_ascii=False),
         json.dumps(list(missing or []), ensure_ascii=False), note,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), by))
    conn.commit()
    return cur.lastrowid


def list_archive(conn, ip: str) -> list[dict]:
    """某台 switch 的所有存檔（新到舊），不含原文本體（原文另外下載）。"""
    conn.execute(ARCHIVE_SQL)
    rows = conn.execute(
        "SELECT id, ip, kind, ok, length(content) AS size, recognized, missing, note, "
        "created_at, created_by FROM san_import_archive WHERE ip = ? ORDER BY id DESC", (ip,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("recognized", "missing"):
            try:
                d[k] = json.loads(d[k] or "[]")
            except ValueError:
                d[k] = []
        d["kind_label"] = {"offline_import": "離線匯入", "online_collect": "線上收集"}.get(d["kind"], d["kind"])
        out.append(d)
    return out


def get_archive(conn, archive_id: int) -> dict | None:
    conn.execute(ARCHIVE_SQL)
    r = conn.execute("SELECT * FROM san_import_archive WHERE id = ?", (archive_id,)).fetchone()
    return dict(r) if r else None
