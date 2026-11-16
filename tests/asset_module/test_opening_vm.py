"""開盤摘要第二段：VM 使用超過主機實體 CPU 10%（2.059.10，使用者 11-10「做第二段」）。

口徑＝主機組人工報告：VM 的 cpu|usagemhz_average（vCOps rollUpType=MAX、5 分鐘）09:00～09:30 最高 ÷ 主機 cpu|totalCapacity。
釘住：佔比算法、門檻 10% 含等於、由高到低、用途／部門／負責人的取值順序、沒帶 VM＝不知道（不是 0 台）、
舊批次（沒有 vms 欄）照樣匯得進來、Teams 卡片有這一段。主機名用 esxi99-*.example.test。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import credential_store  # noqa: E402
import opening_notify as on  # noqa: E402
import opening_peak as op  # noqa: E402
import teams_notify as tn  # noqa: E402
from test_opening_notify import REAL, queued, teams_on  # noqa: E402,F401
from test_opening_peak import DAY, env as _env, host, imp, series, write_batch  # noqa: E402,F401


@pytest.fixture
def env(_env, tmp_path, monkeypatch):
    monkeypatch.setattr(credential_store, "KEY_PATH_DEFAULT", str(tmp_path / "k.key"))
    return _env


def vm(name, mhz):
    return {"name": name, "resource_id": "vm-" + name, **series(DAY, mhz)}


def esxi(name, cap=50000, vms=None, cpu=(50, 60, 70, 80, 85, 60)):
    h = host(name, cpu=list(cpu), mem=[50] * 6)
    if cap is not None:
        h["metrics"][op.CAP_KEY] = series(DAY, [cap] * 6)
    if vms is not None:
        h["vms"] = vms
    return h


def asset(c, serial, hostname, purpose=None, unit=None, custodian=None, api_id=None):
    c.execute("INSERT INTO hardware (asset_serial, hostname, ip, asset_purpose, usage_unit, custodian, api_id, asset_status) "
              "VALUES (?,?,?,?,?,?,?,'使用中')", (serial, hostname, None, purpose, unit, custodian, api_id))
    c.commit()


def test_佔比算法_門檻含等於_由高到低(env):
    c, drop = env
    # 容量 50000 MHz：10000＝20%、5000＝10%（含等於）、4999＝9.998% 不列；09:30 以後的點不算
    write_batch(drop, "b1", [esxi("esxi99-001.example.test", vms=[
        vm("vm-a", [1000, 10000, 2000, 0, 0, 0]),
        vm("vm-b", [5000, 0, 0, 0, 0, 0]),
        vm("vm-c", [4999, 0, 0, 0, 0, 0]),
        {"name": "vm-late", "resource_id": "x", **series(DAY, [0, 0, 0, 0, 0, 0, 40000])},
    ])])
    assert imp(c)["imported"] == 1
    vr = op.vm_rows(c, DAY)
    assert [(r["vm"], r["pct"], r["max_at"]) for r in vr["rows"]] == [("vm-a", 20.0, "09:05"), ("vm-b", 10.0, "09:00")]
    assert vr["hosts_with_vms"] == 1 and vr["missing_hosts"] == []
    assert [r["__no"] for r in vr["rows"]] == [1, 2]


def test_用途部門負責人_業務系統優先_沒有才用資產_都沒有寫未提供(env):
    c, drop = env
    asset(c, "HW-1", "vm-a", purpose="下單主機", unit="經紀部", custodian="王小明")
    asset(c, "HW-2", "vm-b")
    write_batch(drop, "b1", [esxi("esxi99-001.example.test", vms=[vm("vm-a", [20000] * 6), vm("vm-b", [15000] * 6)])])
    imp(c)
    by = {r["vm"]: r for r in op.vm_rows(c, DAY)["rows"]}
    assert (by["vm-a"]["purpose"], by["vm-a"]["dept"], by["vm-a"]["owner"]) == ("下單主機", "經紀部", "王小明")
    assert by["vm-a"]["match"] == "matched" and by["vm-a"]["link"]
    assert (by["vm-b"]["purpose"], by["vm-b"]["dept"], by["vm-b"]["owner"]) == ("未提供", "未提供", "未提供")


def test_舊批次沒帶vms_照樣匯入_VM段寫不知道不是沒有(env):
    c, drop = env
    teams_on(c)
    write_batch(drop, "b1", [esxi("esxi99-001.example.test", vms=None, cpu=(95, 20, 20, 20, 20, 20))])
    assert imp(c)["imported"] == 1
    vr = op.vm_rows(c, DAY)
    assert vr["hosts_with_vms"] == 0 and vr["missing_hosts"] == ["esxi99-001.example.test"]
    s = c.execute("SELECT summary FROM opening_notify_seen WHERE day = ?", (DAY,)).fetchone()[0]
    assert "不知道哪些 VM 吃掉主機 CPU" in s and "沒有 VM 使用超過" not in s


def test_有VM資料但都沒超過_寫沒有_部分主機沒讀到要講(env):
    c, drop = env
    teams_on(c)
    write_batch(drop, "b1", [esxi("esxi99-001.example.test", vms=[vm("vm-a", [100] * 6)]),
                             esxi("esxi99-002.example.test", vms=None)])
    imp(c)
    s = c.execute("SELECT summary FROM opening_notify_seen WHERE day = ?", (DAY,)).fetchone()[0]
    assert "沒有 VM 使用超過主機實體 CPU 10%" in s and "1 台 ESXi 的 VM 沒讀到" in s


def test_Teams卡片列出VM_一台一行(env):
    c, drop = env
    teams_on(c)
    asset(c, "HW-1", "vm-a", purpose="下單主機", unit="經紀部", custodian="王小明")
    write_batch(drop, "b1", [esxi("esxi99-001.example.test", vms=[vm("vm-a", [20000] * 6)])])
    imp(c)
    s = c.execute("SELECT summary FROM opening_notify_seen WHERE day = ?", (DAY,)).fetchone()[0]
    assert "VM 使用超過主機實體 CPU 10%：1 台" in s
    assert "vm-a｜esxi99-001｜40%（09:00）｜經紀部｜王小明" in s           # 2.060.x 新格式：用途拿掉、最多 5 行


def test_沒有主機容量_算不出佔比_不列且要講(env):
    c, drop = env
    teams_on(c)
    write_batch(drop, "b1", [esxi("esxi99-001.example.test", cap=None, vms=[vm("vm-a", [20000] * 6)])])
    imp(c)
    vr = op.vm_rows(c, DAY)
    assert vr["rows"] == [] and vr["no_cap_hosts"] == ["esxi99-001.example.test"]
    s = c.execute("SELECT summary FROM opening_notify_seen WHERE day = ?", (DAY,)).fetchone()[0]
    assert "沒有 CPU 總容量，算不出佔比" in s


def test_vms格式壞掉_整批拒收不猜(env):
    c, drop = env
    write_batch(drop, "b1", [esxi("esxi99-001.example.test", vms=[{"name": "", "t": [], "v": []}])])
    out = imp(c)
    assert out.get("imported", 0) == 0


def test_PowerShell單一VM被攤平成物件也收(env):
    c, drop = env
    write_batch(drop, "b1", [esxi("esxi99-001.example.test", vms=vm("vm-a", [20000] * 6))])
    assert imp(c)["imported"] == 1
    assert [r["vm"] for r in op.vm_rows(c, DAY)["rows"]] == ["vm-a"]


def test_vCOps的VM名帶註記_去掉後對得到資產(env):
    # 11-10 55.33 實測：vCOps VM 名「EXSVR348-219_AP04」「EXSVR334-013(eGW-004)」，資產登記的是主機名
    c, drop = env
    asset(c, "HW-1", "EXSVR348-219", purpose="下單AP")
    asset(c, "HW-2", "EXSVR334-013", purpose="電子閘道")
    write_batch(drop, "b1", [esxi("esxi99-001.example.test", vms=[vm("EXSVR348-219_AP04", [20000] * 6),
                                                                 vm("EXSVR334-013(eGW-004)", [15000] * 6),
                                                                 vm("NOASSET_X", [15000] * 6)])])
    imp(c)
    by = {r["vm"]: r for r in op.vm_rows(c, DAY)["rows"]}
    assert by["EXSVR348-219_AP04"]["purpose"] == "下單AP" and by["EXSVR348-219_AP04"]["match"] == "matched"
    assert by["EXSVR334-013(eGW-004)"]["purpose"] == "電子閘道"
    assert by["NOASSET_X"]["match"] == "no_asset" and by["NOASSET_X"]["purpose"] == "未提供"
