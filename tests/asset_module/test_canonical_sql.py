"""SQL 版判定必須跟 Python 正典完全同義（2026-09-18）。

背景：is_vm 與「帳外」判定以前在 api／blast_radius／hardware_master 各寫一段 SQL，
其中 is_vm 的 SQL 漏了 device_model「(VM)」標記 → 跟儀表板（is_vm_value）差 1,188 台。
統一成 manage_state.is_vm_sql／system_report.off_book_sql 之後，這支釘住「SQL＝Python」，
任何一邊再改而沒同步，parity 就紅。
"""
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACKEND))

import db  # noqa: E402
import manage_state as ms  # noqa: E402
import system_report as sr  # noqa: E402

# 涵蓋各種變體：is_vm 欄的多種寫法 × device_model 標記
VM_CASES = [
    ("1", None), ("0", None), ("VM", None), ("是", None), ("否", None),
    ("TRUE", None), ("false", None), (None, None), ("", None),
    (None, "(VM)"), ("0", "(VM)"), (None, "VM-Series"), (None, "VM"),
    (None, "Dell R740"), (None, "ATEN KVM"), (None, "Server KVM"),  # KVM 不可誤判成 VM
    (0, "(VM) something"), (None, "vm-x"),  # 小寫也要認（is_vm_value 用 upper）
]
SN_CASES = ["DYN-1", "VC-2", "AUTO-3", "HW-4", "", None, "dyn-lower", "XDYN-5"]


def _fixture(tmp_path):
    dbp = tmp_path / "c.db"
    db.init_db(dbp)
    conn = db.get_connection(dbp)
    for i, (v, dm) in enumerate(VM_CASES):
        conn.execute("INSERT INTO hardware (asset_serial, is_vm, device_model) VALUES (?,?,?)",
                     (f"VM-{i}", v, dm))
    for i, sn in enumerate(SN_CASES):
        if sn is not None:
            conn.execute("INSERT INTO hardware (asset_serial, is_vm) VALUES (?, '0')", (sn,))
    conn.commit()
    return conn


def test_is_vm_sql_等於_is_vm_value(tmp_path):
    conn = _fixture(tmp_path)
    sql_vm = {r[0] for r in conn.execute(
        f"SELECT asset_serial FROM hardware WHERE {ms.is_vm_sql()}")}
    py_vm = {r["asset_serial"] for r in conn.execute(
        "SELECT asset_serial, is_vm, device_model FROM hardware")
        if ms.is_vm_value(r["is_vm"], r["device_model"])}
    assert sql_vm == py_vm, f"SQL 與 is_vm_value 不一致：只SQL {sql_vm - py_vm}，只Py {py_vm - sql_vm}"


def test_off_book_sql_等於_is_off_book(tmp_path):
    conn = _fixture(tmp_path)
    sql_ob = {r[0] for r in conn.execute(
        f"SELECT asset_serial FROM hardware WHERE {sr.off_book_sql()}")}
    py_ob = {r["asset_serial"] for r in conn.execute("SELECT asset_serial FROM hardware")
             if sr.is_off_book(r["asset_serial"])}
    assert sql_ob == py_ob, f"帳外 SQL 與 is_off_book 不一致：只SQL {sql_ob - py_ob}，只Py {py_ob - sql_ob}"


def test_alias_欄名參數可用(tmp_path):
    """帶表別名（h.is_vm）也要能組出正確 SQL。"""
    conn = _fixture(tmp_path)
    n = conn.execute(
        f"SELECT COUNT(*) FROM hardware h WHERE {ms.is_vm_sql('h.is_vm', 'h.device_model')}"
    ).fetchone()[0]
    py = sum(1 for r in conn.execute("SELECT is_vm, device_model FROM hardware")
             if ms.is_vm_value(r["is_vm"], r["device_model"]))
    assert n == py
