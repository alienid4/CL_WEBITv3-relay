"""機房搬遷 CIA 盤點表匯出。

依「青埔機房搬遷_CIA資產_Master.xlsx」的 4 張表（Server Master / LAN Mapping /
FC SAN Mapping / Power Mapping）欄位，把我們系統現有資料**預填**成同一個格式，讓現場
只要補實體/網路欄位、改帳實不一致的地方，不用從零抄。

範圍：實體 Server（is_vm 非 1）——搬遷是逐台實體下架上架，VM 隨宿主機走、不單獨列。
可用 location 篩選（例如只出某機房）。欄位標頭是通用欄名、無真實識別字。
"""
from __future__ import annotations

import io
from datetime import datetime

SERVER_COLS = [
    "資產序號", "資產狀態", "資產分類", "資產名稱", "主機名稱", "APID", "環境別", "使用單位",
    "擁有者", "保管者", "使用者", "所屬公司", "廠牌", "型號", "Serial Number", "CPU", "RAM",
    "Storage", "OS", "IP(管理/主要)", "原機房", "原Rack", "原U位", "設備U數", "新機房", "新Rack",
    "新U位", "LAN Port數", "FC Port數", "PSU數", "搬遷批次", "搬遷狀態", "現場盤點日期", "盤點人",
    "CIA差異狀態", "備註",
]
LAN_COLS = [
    "資產序號", "主機名稱", "NIC名稱", "Physical Port", "MAC", "IP", "Subnet", "Gateway", "VLAN",
    "Bond/Team", "Bond模式", "vSwitch", "Port Group", "VMkernel", "原Switch", "原Switch Port",
    "新Switch", "新Switch Port", "線材類型", "速率", "盤點狀態", "備註",
]
FC_COLS = [
    "資產序號", "主機名稱", "HBA名稱", "Physical Port", "WWPN", "WWNN", "Fabric(A/B)",
    "原SAN Switch", "原Switch Port", "新SAN Switch", "新Switch Port", "Zone Name", "Storage",
    "Storage Port", "Target WWPN", "速率", "盤點狀態", "備註",
]
PWR_COLS = [
    "資產序號", "主機名稱", "PSU", "A/B路", "原PDU", "原PDU Port", "原電源迴路", "新PDU",
    "新PDU Port", "接頭/線材", "盤點狀態", "備註",
]
CIA_DIFF_COLS = [
    "資產序號", "主機名稱", "欄位名稱", "CIA原值", "現場盤點值", "差異類型", "是否需CIA異動",
    "申請單編號", "處理狀態", "完成日期", "備註",
]
CHECKLIST_COLS = [
    "資產序號", "主機名稱", "搬遷批次", "備份確認", "線材標籤", "MAC/WWPN確認", "關機確認",
    "下架確認", "運送確認", "新Rack/U確認", "電源A/B確認", "LAN確認", "FC Path確認",
    "OS開機確認", "Ping確認", "Bond/Team確認", "vSwitch/VMkernel確認", "Application確認",
    "完成狀態", "異常/備註",
]
# CIA 資產基準（原始登記快照）；(欄名, hardware 欄位) 對照，特殊值在 build 裡處理。
CIA_BASE_MAP = [
    ("盤點單位-處別", "inventory_division"), ("盤點單位-部門", "inventory_department"),
    ("資產序號", "asset_serial"), ("資產狀態", "asset_status"), ("群組名稱", "group_name"),
    ("APID", "api_id"), ("資產名稱", "asset_name"), ("整體基礎架構", "infra_type"),
    ("設備機型", "device_model"), ("資產用途", "asset_purpose"), ("資產實體位置", "physical_location"),
    ("機櫃編號", "rack_no"), ("數量", "quantity"), ("擁有者", "owner"), ("環境別", "environment"),
    ("主機名稱", "hostname"), ("作業系統", "os"), ("BIG IP/VIP", "big_ip_vip"),
    ("硬體編號", "hardware_no"), ("IP", "ip"), ("保管者", "custodian"), ("使用單位", "usage_unit"),
    ("使用者", "user_name"), ("附加說明", "remark"), ("所屬公司", "owning_company"),
    ("完整性(I)", "integrity"), ("機密性(C)", "confidentiality"), ("可用性(A)", "availability"),
    ("申請單編號", "request_no"),
]

# 廠牌從「設備機型」字串解析（型號通常帶廠牌，如 "Lenovo x3550 M5"）。收不到就留白，不亂猜。
_BRANDS = [
    ("hewlett packard", "HPE"), ("hpe", "HPE"), ("proliant", "HPE"), ("hp ", "HP"),
    ("lenovo", "Lenovo"), ("ibm", "IBM"), ("dell", "Dell"), ("fujitsu", "Fujitsu"),
    ("huawei", "Huawei"), ("inspur", "Inspur"), ("supermicro", "Supermicro"),
    ("oracle", "Oracle"), ("sun ", "Oracle/Sun"), ("nec", "NEC"), ("hitachi", "Hitachi"),
    ("netapp", "NetApp"), ("emc", "EMC"), ("acer", "Acer"), ("asus", "ASUS"),
    # 網通/資安設備（型號常不帶廠牌字，補常見前綴）
    ("cisco", "Cisco"), ("catalyst", "Cisco"), ("nexus", "Cisco"), ("ws-c", "Cisco"),
    ("fortinet", "Fortinet"), ("fortigate", "Fortinet"), ("forti", "Fortinet"),
    ("juniper", "Juniper"), ("aruba", "Aruba"), ("arista", "Arista"),
    ("palo alto", "Palo Alto"), ("check point", "Check Point"), ("checkpoint", "Check Point"),
    ("f5 ", "F5"), ("big-ip", "F5"), ("netscaler", "Citrix"), ("brocade", "Brocade"),
    ("qnap", "QNAP"), ("synology", "Synology"),
]


def _brand(device_model: str | None) -> str:
    if not device_model:
        return ""
    s = " " + device_model.lower() + " "
    for kw, name in _BRANDS:
        if kw in s:
            return name
    return ""


# 每台預設要盤幾個 port（跟 Master 的骨架一致：LAN 4 / FC 2 / PSU 2）；現場可增列。
DEFAULT_LAN_PORTS = 4
DEFAULT_FC_PORTS = 2
DEFAULT_PSU = 2


# VM 判定：is_vm 旗標常跟 device_model 矛盾（實測 1159 台 device_model 是「(VM)」卻 is_vm=0），
# 所以旗標或型號任一顯示是 VM 就算 VM。用來標「資產分類」，以及決定 LAN/FC/Power 骨架要不要出。
def _is_vm(h) -> bool:
    if h["is_vm"] == 1:
        return True
    dm = (h["device_model"] or "").strip().lower()
    return dm in ("(vm)", "vm") or "virtual" in dm


# 實體機（有實體纜線/機櫃/HBA/PSU 要盤的）：LAN/FC/Power 骨架只出這些。
_PHYS_WHERE = ("(is_vm IS NULL OR is_vm != 1) AND asset_serial IS NOT NULL "
               "AND COALESCE(device_model,'') NOT IN ('(VM)','VM') "
               "AND lower(COALESCE(device_model,'')) NOT LIKE '%virtual%'")


def _all_rows(conn, location: str | None):
    """全部資產（實體＋VM）——Server Master／Checklist 用（2026-09-14 使用者：全部都要）。"""
    q = "SELECT * FROM hardware WHERE asset_serial IS NOT NULL"
    params: list = []
    if location:
        q += " AND physical_location LIKE ?"
        params.append(f"%{location}%")
    q += " ORDER BY physical_location, rack_no, hostname"
    return conn.execute(q, params).fetchall()


def _rows(conn, location: str | None):
    """只實體——LAN/FC/Power 骨架用。"""
    q = f"SELECT * FROM hardware WHERE {_PHYS_WHERE}"
    params: list = []
    if location:
        q += " AND physical_location LIKE ?"
        params.append(f"%{location}%")
    q += " ORDER BY physical_location, rack_no, hostname"
    return conn.execute(q, params).fetchall()


def _host_specs(conn) -> dict:
    """已納管主機收到的硬體規格（host_spec），以資產序號為 key，給匯出填 CPU/RAM/Storage。"""
    try:
        return {r["asset_serial"]: r for r in conn.execute("SELECT * FROM host_spec")}
    except Exception:   # noqa: BLE001 — 舊庫還沒這張表就當作沒有
        return {}


def _norm(v) -> str:
    return str(v).strip().lower() if v not in (None, "") else ""


def _compare(reg, got):
    """回 'green'(一致) / 'red'(不一致) / ''(無法比：其中一邊沒有)。"""
    if got in (None, ""):
        return ""
    if reg in (None, ""):
        return ""          # 原本沒登記、這次盤到 → 不算不一致，當補值（中性）
    return "green" if _norm(reg) == _norm(got) else "red"


def build(conn, location: str | None = None) -> tuple[io.BytesIO, int]:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    rows = _all_rows(conn, location)        # Server Master／Checklist：全部資產
    phys_rows = _rows(conn, location)       # LAN/FC/Power：只實體
    specs = _host_specs(conn)

    RED = Font(color="C00000")      # 核對過、不一致（矯正/待確認）
    GREEN = Font(color="2E7D32")    # 核對過、一致
    BLUE = Font(color="1565C0")     # 系統補上的新值（登記本來沒有）
    GREY = Font(color="9AA39F")     # 未核對（待收集/待現場）
    HEAD_FILL = PatternFill("solid", fgColor="00806A")
    HEAD_FONT = Font(color="FFFFFF", bold=True, size=10)

    diffs: list[list] = []   # 累積到 CIA待異動：跑到不一致就記一列

    def style_header(ws, cols):
        for c, name in enumerate(cols, 1):
            cell = ws.cell(1, c)
            cell.fill = HEAD_FILL
            cell.font = HEAD_FONT
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            ws.column_dimensions[cell.column_letter].width = max(10, min(20, len(name) + 3))
        ws.freeze_panes = "A2"

    wb = Workbook()

    # ---- 1. Dashboard ----
    dash = wb.active
    dash.title = "Dashboard"
    dash.append(["機房搬遷 CIA 資產盤點", ""])
    dash.append(["產生時間", datetime.now().strftime("%Y-%m-%d %H:%M")])
    dash.append(["範圍", location or "全部機房"])
    dash.append(["實體 Server 台數", len(rows)])
    dash.append(["已收硬體規格台數", len(specs)])
    dash.append([""])
    dash.append(["顏色說明", "綠字＝CIA原值與系統盤到的一致；紅字＝不一致(見 CIA待異動)；留白＝待現場盤點"])
    dash.cell(1, 1).font = Font(bold=True, size=14, color="00806A")

    # ---- 2. Server Master（含紅綠比對）----
    ws = wb.create_sheet("Server Master")
    ws.append(SERVER_COLS)
    style_header(ws, SERVER_COLS)
    col_idx = {name: i + 1 for i, name in enumerate(SERVER_COLS)}
    for h in rows:
        cls = "VM" if _is_vm(h) else "實體Server"
        sp = specs.get(h["asset_serial"])
        brand = (sp["vendor"] if sp and sp["vendor"] else "") or _brand(h["device_model"])
        cpu = (sp["cpu"] if sp and sp["cpu"] else "")
        if sp and sp["cores"]:
            cpu = (f"{cpu}（{sp['cores']} 核）" if cpu else f"{sp['cores']} 核")
        ram = (f"{round(sp['mem_mb'] / 1024)} GB" if sp and sp["mem_mb"] else "")
        storage = (sp["storage"] if sp and sp["storage"] else "")
        # Serial：以盤到的為顯示值、跟登記比對上色
        got_sn = sp["serial"] if sp and sp["serial"] else ""
        sn = got_sn or (h["hw_serial"] or "")
        ws.append([
            h["asset_serial"], h["asset_status"], cls, h["asset_name"], h["hostname"],
            h["api_id"], h["environment"], h["usage_unit"], h["owner"], h["custodian"],
            h["user_name"], h["owning_company"], brand, h["device_model"], sn,
            cpu, ram, storage, h["os"], h["ip"],
            h["physical_location"], h["rack_no"], "", "", "", "", "",
            DEFAULT_LAN_PORTS, DEFAULT_FC_PORTS, DEFAULT_PSU,
            "", "待盤點", "", "", "待比對", h["remark"],
        ])
        r = ws.max_row
        # 逐欄比對：登記值 vs 系統盤到值 → 上色 + 記差異
        checks = [
            ("Serial Number", h["hw_serial"], got_sn),
            ("型號", h["device_model"], (sp["model"] if sp and sp["model"] else "")),
        ]
        n_ok = n_diff = 0
        for field, reg, got in checks:
            verdict = _compare(reg, got)
            if verdict == "green":
                ws.cell(r, col_idx[field]).font = GREEN; n_ok += 1
            elif verdict == "red":
                ws.cell(r, col_idx[field]).font = RED; n_diff += 1
                diffs.append([h["asset_serial"], h["hostname"], field, reg, got,
                              "不一致", "是", "", "待處理", "", ""])
        # 系統補上的實測新值（登記本來沒有）→ 藍字
        n_new = 0
        for field, val in (("CPU", cpu), ("RAM", ram), ("Storage", storage)):
            if val:
                ws.cell(r, col_idx[field]).font = BLUE; n_new += 1
        # 本列核對狀態（寫進「CIA差異狀態」欄，讓每台一眼看出核對到什麼程度）
        st = ws.cell(r, col_idx["CIA差異狀態"])
        if n_diff:
            st.value = f"不一致（{n_diff} 欄）"; st.font = RED
        elif n_ok:
            st.value = "核對一致" + (f"＋補值{n_new}" if n_new else ""); st.font = GREEN
        elif n_new:
            st.value = f"已補值（{n_new} 欄）"; st.font = BLUE
        else:
            st.value = "待收集"; st.font = GREY

    # ---- 3~5. LAN / FC SAN / Power（骨架，待現場）----
    lan = wb.create_sheet("LAN Mapping"); lan.append(LAN_COLS); style_header(lan, LAN_COLS)
    for h in phys_rows:
        for n in range(1, DEFAULT_LAN_PORTS + 1):
            mac = h["mac"] if (n == 1 and h["mac"]) else ""
            ip = h["ip"] if (n == 1 and h["ip"]) else ""
            lan.append([h["asset_serial"], h["hostname"], f"NIC{n}", f"Port {n}", mac, ip,
                        h["subnet"] if n == 1 else "", "", "", "", "", "", "", "", "", "",
                        "", "", "", "", "待盤點", ""])

    fc = wb.create_sheet("FC SAN Mapping"); fc.append(FC_COLS); style_header(fc, FC_COLS)
    for h in phys_rows:
        for i, fab in ((1, "A"), (2, "B")):
            fc.append([h["asset_serial"], h["hostname"], f"HBA{i}", f"FC Port {i}", "", "", fab,
                       "", "", "", "", "", "", "", "", "", "待盤點", ""])

    pw = wb.create_sheet("Power Mapping"); pw.append(PWR_COLS); style_header(pw, PWR_COLS)
    for h in phys_rows:
        for i, ab in ((1, "A"), (2, "B")):
            pw.append([h["asset_serial"], h["hostname"], f"PSU{i}", ab, "", "", "", "", "", "",
                       "待盤點", ""])

    # ---- 6. CIA待異動（自動帶入偵測到的不一致）----
    cd = wb.create_sheet("CIA待異動"); cd.append(CIA_DIFF_COLS); style_header(cd, CIA_DIFF_COLS)
    for d in diffs:
        cd.append(d)
        cd.cell(cd.max_row, 3).font = RED
    if not diffs:
        cd.append(["（目前系統盤到的與 CIA 原值沒有不一致；現場盤點後如有差異再補）"])

    # ---- 7. 搬遷Checklist（每台一列，確認欄待勾）----
    ck = wb.create_sheet("搬遷Checklist"); ck.append(CHECKLIST_COLS); style_header(ck, CHECKLIST_COLS)
    for h in rows:
        ck.append([h["asset_serial"], h["hostname"]] + [""] * (len(CHECKLIST_COLS) - 2))

    # ---- 8. 欄位說明（顏色圖例）----
    lg = wb.create_sheet("欄位說明"); lg.append(["分類", "顏色", "用途/盤點重點"]); style_header(lg, ["分類", "顏色", "用途/盤點重點"])
    for cat, color, note, font in [
        ("核對一致", "綠字", "系統有盤到、且與 CIA 登記值一致（已核對）", GREEN),
        ("不一致(已矯正)", "紅字", "系統盤到的與 CIA 登記不同，值以盤到的為準、並列進 CIA待異動分頁", RED),
        ("系統補值", "藍字", "登記本來沒有、系統這次盤到補上（如 CPU/RAM/Storage）", BLUE),
        ("待收集/待現場", "灰字/留白", "系統還沒盤到可比對的值，或本來就得靠現場填（U 位、Switch Port、WWPN、PDU…）", GREY),
    ]:
        lg.append([cat, color, note])
        if font:
            lg.cell(lg.max_row, 2).font = font

    # ---- 9. CIA資產基準（原始登記快照，全部資產）----
    base = wb.create_sheet("CIA資產基準")
    base_cols = [c[0] for c in CIA_BASE_MAP] + ["資產分類", "本次盤點狀態", "盤點日期", "盤點人員"]
    base.append(base_cols); style_header(base, base_cols)
    for h in conn.execute("SELECT * FROM hardware WHERE asset_serial IS NOT NULL "
                          "ORDER BY physical_location, hostname"):
        row = [h[col] for _, col in CIA_BASE_MAP]
        row += ["VM" if (h["is_vm"] == 1) else "實體Server", "", "", ""]
        base.append(row)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf, len(rows)


def locations(conn) -> list[dict]:
    """各機房的實體 Server 台數，給匯出頁的下拉。"""
    q = ("SELECT COALESCE(physical_location,'（未填機房）') AS loc, COUNT(*) AS n "
         "FROM hardware WHERE asset_serial IS NOT NULL "
         "GROUP BY loc ORDER BY n DESC")
    return [dict(r) for r in conn.execute(q)]
