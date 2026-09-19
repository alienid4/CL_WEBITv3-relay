"""每一種匯入都配一份範例檔（2026-09-10 使用者：「每一個都要範例檔案，
不然 USER 不會知道要匯入什麼資料」）。

## 範例列為什麼以「範例」開頭

範例檔下載回去，最常見的錯是**原封不動匯回來**（或只在下面加幾列、沒刪範例列）。
所以每一份範例的鍵值都以「範例」開頭，各匯入程式看到這種鍵就略過並回報
（`is_example`），不會長出一個叫「範例系統A」的假系統或假資產。
真實代碼是 N-／I- 開頭、資產序號是數字，不會跟「範例」撞。

## 表頭從各匯入程式的欄位定義產生，不另外抄一份

抄一份的話，匯入程式改了欄名，範例檔不會跟著改，範例就變成匯不進去的範例。
"""
from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook

EXAMPLE_PREFIX = "範例"


def is_example(value: Any) -> bool:
    """這個鍵值是不是範例檔帶進來的範例列。"""
    return isinstance(value, str) and value.strip().startswith(EXAMPLE_PREFIX)


def _sys_class() -> list[tuple[str, list, list[list]]]:
    return [("系統類別", ["項次", "系統類別", "APID", "系統別"], [
        [1, "S1-第一類系統軟體（核心）", "範例-001", "範例系統A"],
        [2, "S2-第二類系統軟體", "範例-002", "範例系統B"],
        [3, "S3-第三類系統軟體", "範例-003", "範例系統C"],
    ])]


def _business_system() -> list[tuple[str, list, list[list]]]:
    return [("業務系統對照表", ["system_id", "system", "ap_department", "ap_owner"], [
        ["範例-001", "範例系統A", "範例部門", "範例負責人"],
        ["範例-002", "範例系統B", "範例部門", "範例負責人"],
    ])]


def _rvtools() -> list[tuple[str, list, list[list]]]:
    import rvtools_import

    headers: list[str] = []
    for cands in rvtools_import._COLUMN_CANDIDATES.values():
        if cands[0] not in headers:
            headers.append(cands[0])
    example = {
        "VM": "範例-VM01", "DNS Name": "example-vm01.example.local",
        "Primary IP Address": "192.0.2.10", "OS according to the VMware Tools": "Rocky Linux 9 (64-bit)",
        "Host": "esxi-example-01", "Powerstate": "poweredOn", "VM ID": "vm-1001",
        "VM UUID": "42000000-0000-0000-0000-000000000001", "Cluster": "EXAMPLE-CLUSTER",
        "VI SDK Server": "vcenter-example.local",
        "Path": "[EXAMPLE_Datastore] 範例-VM01/範例-VM01.vmx",
    }
    return [("vInfo", headers, [[example.get(h, "") for h in headers]])]


def _cia_excel() -> list[tuple[str, list, list[list]]]:
    import excel_import

    mapping = excel_import.load_mapping()
    example = {
        "asset_serial": "範例-0001", "asset_name": "範例主機", "hostname": "example-host01",
        "ip": "192.0.2.20", "os": "Rocky Linux 9", "environment": "正式",
        "physical_location": "範例機房", "api_id": "範例-001", "asset_status": "使用中",
        "quantity": 1,
    }
    sheets = []
    for sheet in excel_import.SHEET_CONFIG:
        cols = mapping.get(sheet, {})
        headers = list(cols.keys())
        row = [example.get(cols[h], "") for h in headers]
        sheets.append((sheet, headers, [row]))
    return sheets


#: source key（跟匯出那邊的 key 一致）→ (範例檔名, 產生器)
TEMPLATES = {
    "sys_class": ("系統類別對照表_範例.xlsx", _sys_class),
    "business_system": ("業務系統對照表_範例.xlsx", _business_system),
    "rvtools": ("RVTools_範例.xlsx", _rvtools),
    "cia_excel": ("CIA資產清冊_範例.xlsx", _cia_excel),
}


def build(source: str) -> tuple[str, bytes]:
    """回 (檔名, xlsx 內容)。不認得的來源丟 KeyError。"""
    fname, gen = TEMPLATES[source]
    wb = Workbook()
    wb.remove(wb.active)
    for title, headers, rows in gen():
        ws = wb.create_sheet(title)
        ws.append(headers)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return fname, buf.getvalue()
