"""對帳橋 v1.502.3：獨立審查（2026-10-26）擋下 fee451a 的各項，一項一個測試。

H1 對帳分母錨錯掃描（MAX(scan_time) 是中午 1 段補掃）／M1 VM、伺服器被當成管理口／M2 人工指定代表筆會把重複登記升格／
M3 「同 U 只有一台」把別家的管理口併進去／L1 同一個箱子的代表挑法不固定／L2 一體機元件、疑似登記錯誤裡跑 OS 的算主機／
L3 人工指定要過寫入關卡、失效的角色不可以 500／L4 復原的檢查跟寫入同一個交易。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import count_bridge as cb  # noqa: E402
import db  # noqa: E402
import overlap_queue  # noqa: E402
import rack_layout as rl  # noqa: E402
import scan_scope  # noqa: E402
import test_count_bridge as base  # noqa: E402


def _ago(days: float) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _blank(tmp_path):
    p = tmp_path / "r.db"
    db.init_db(p)
    return db.get_connection(p)


def _hw(conn, serial, host, ip, rack=None, model=None, os=None, **kw):
    db.insert_hardware(conn, asset_serial=serial, hostname=host, ip=ip, physical_location="01_甲機房",
                       rack_no=rack, device_model=model, os=os, **kw)


# ---------------------------------------------------------------- H1
def test_H1_對帳分母用這一輪每段最近一次有結果的掃描_不是最後一次補掃(tmp_path):
    conn, _ = base._conn(tmp_path)
    scan_scope._ensure(conn)
    full, adhoc, empty = _ago(2), _ago(1), _ago(0.1)

    def cov(t, cidr):
        conn.execute("INSERT INTO scan_coverage (scan_time, cidr, ok, created_at) VALUES (?,?,1,?)", (t, cidr, t))

    def alive(t, ip, host, seg):
        conn.execute("INSERT INTO scan_history (scan_time, ip, hostname, segment, scan_ok) VALUES (?,?,?,?,1)",
                     (t, ip, host, seg))
    # 凌晨全網段：.1／.2／.3 三段，srv-a 跟 vm1 回應
    for c in ("10.99.1.0/24", "10.99.2.0/24", "10.99.3.0/24"):
        cov(full, c)
    alive(full, "10.99.1.1", "srv-a", "10.99.1.0/24")
    alive(full, "10.99.3.1", "vm1", "10.99.3.0/24")
    # 中午補掃：只有 .6 一段，lonely 回應（MAX(scan_time) 在這之後還有一次空的）
    cov(adhoc, "10.99.6.0/24")
    alive(adhoc, "10.99.6.1", "lonely", "10.99.6.0/24")
    # 最後一次排程：四段都記「掃過」，但一台都沒回（掃描器壞了）→ 不能當涵蓋
    for c in ("10.99.1.0/24", "10.99.2.0/24", "10.99.3.0/24", "10.99.6.0/24"):
        cov(empty, c)
    # 只補掃 1 段（.4）也 0 台 → 可能整段斷線：照算涵蓋（那段的機器算搜不到）、列出來標紅（再審）
    down = _ago(0.5)
    cov(down, "10.99.4.0/24")
    conn.commit()
    rc = cb.recon_coverage(conn)
    by = {x["asset_serial"]: x["bucket"] for x in rc["items"]}
    assert by["HW-SRV"] == "overlap" and by["HW-VM"] == "overlap" and by["HW-LONE"] == "overlap"
    assert by["HW-STO"] == "missing"                      # .1 段有涵蓋、沒回應
    assert by["HW-PC2"] == "missing"                      # .4 段：可能整段斷線，照算涵蓋
    assert rc["suspect_down"] == [{"cidr": "10.99.4.0/24", "scan_time": down}]
    assert rc["covered_segments"] == 5 >= rc["latest_full"]["segments"] == 3
    assert [(x["scan_time"], x["kind"]) for x in rc["empty_runs"]] == [(empty, "skipped"), (down, "suspect_down")]
    # 依據日期只取真的有結果的掃描（不含那次 0 台的補掃）
    assert rc["scan_time"] == adhoc and rc["scan_from"] == full


def test_小的0台補掃_前後24小時沒有掃到東西_算掃描器故障略過(tmp_path):
    """決定（2026-10-26）：只有掃描器當時確實在動（±24h 內有掃到東西的掃描），小的 0 台補掃才算可能整段斷線。"""
    conn, _ = base._conn(tmp_path)
    scan_scope._ensure(conn)
    full, outage = _ago(5), _ago(1)          # 有結果的全網段在 5 天前；1 天前那次補掃前後 24h 沒有任何有結果的掃描
    for c in ("10.99.1.0/24", "10.99.4.0/24"):
        conn.execute("INSERT INTO scan_coverage (scan_time, cidr, ok, created_at) VALUES (?,?,1,?)", (full, c, full))
    conn.execute("INSERT INTO scan_history (scan_time, ip, hostname, segment, scan_ok) VALUES (?,?,?,?,1)",
                 (full, "10.99.4.1", "pc01", "10.99.4.0/24"))
    conn.execute("INSERT INTO scan_coverage (scan_time, cidr, ok, created_at) VALUES (?,?,1,?)",
                 (outage, "10.99.4.0/24", outage))
    conn.commit()
    rc = cb.recon_coverage(conn)
    by = {x["asset_serial"]: x["bucket"] for x in rc["items"]}
    assert by["HW-PC2"] == "overlap"                      # 照前一次有結果的掃描（5 天前看得到它）
    assert rc["suspect_down"] == []
    assert [(x["scan_time"], x["kind"]) for x in rc["empty_runs"]] == [(outage, "skipped_outage")]


def test_小的0台補掃_中間夾著全網段0台_也算掃描器故障(tmp_path):
    """221 的情形：10-22 13:22 有結果 → 10-23 01:15 全網段 0 台 → 10-23 13:20 補掃 0 台（離有結果的那次不到 24h）。
    中間那次已經證明掃描器壞了，補掃不能當成「那段斷線」。"""
    conn, _ = base._conn(tmp_path)
    scan_scope._ensure(conn)
    ok, broken, adhoc = _ago(1.4), _ago(1.0), _ago(0.5)

    def cov(t, c):
        conn.execute("INSERT INTO scan_coverage (scan_time, cidr, ok, created_at) VALUES (?,?,1,?)", (t, c, t))
    cov(ok, "10.99.4.0/24")
    conn.execute("INSERT INTO scan_history (scan_time, ip, hostname, segment, scan_ok) VALUES (?,?,?,?,1)",
                 (ok, "10.99.4.1", "pc01", "10.99.4.0/24"))
    for c in ("10.99.1.0/24", "10.99.2.0/24", "10.99.3.0/24", "10.99.4.0/24"):
        cov(broken, c)
    cov(adhoc, "10.99.4.0/24")
    conn.commit()
    rc = cb.recon_coverage(conn)
    kinds = {x["scan_time"]: x["kind"] for x in rc["empty_runs"]}
    assert kinds == {broken: "skipped", adhoc: "skipped_outage"}
    assert rc["suspect_down"] == []
    assert {x["asset_serial"]: x["bucket"] for x in rc["items"]}["HW-PC2"] == "overlap"


# ---------------------------------------------------------------- M1
def test_M1_VM與伺服器OS不是管理口(tmp_path):
    assert not rl.is_mgmt_record({"name": "SECOOB100-061", "os": "Windows Server 2019 Standard", "model": "DELL R740"})
    assert not rl.is_mgmt_record({"name": "SVROOB103-100", "os": None, "model": "(VM)"})
    assert not rl.is_mgmt_record({"name": "SECSVR194-021", "_oses": ["Red Hat Enterprise Linux 8"],
                                  "asset_names": ["硬體遠端管理"], "model": "HPE DL380"})
    assert rl.is_mgmt_record({"name": "app01-idrac", "os": "iDRAC9", "model": "DELL"})
    # 再審：強訊號（-ilom 尾巴）贏過伺服器 OS／VM 的排除；主機＋它的 iDRAC 用同一個主機名登記（OS 混著）＝主機
    assert rl.is_mgmt_record({"name": "x-ilom", "os": "Oracle Linux 8", "model": "Oracle ODA"})
    assert rl.is_mgmt_record({"name": "y-idrac", "os": None, "model": "(VM)"})
    assert not rl.is_mgmt_record({"name": "SECSVR194-021", "os": "Idrac 7.00, Windows Server 2022", "model": "DELL R750",
                                  "asset_names": ["內湖共用資料庫4", "硬體遠端管理"]})
    conn = _blank(tmp_path)
    _hw(conn, "M-VM", "SECOOB1-01", "10.99.1.1", is_vm="1", os="Windows Server 2022")
    _hw(conn, "M-SV", "SECOOB1-02", "10.99.1.2", rack="M19/1U", model="DELL R740", os="Windows Server 2019 Standard")
    _hw(conn, "M-PT", "x-idrac", "10.99.1.3", os="iDRAC9")
    conn.commit()
    m = {x["asset_serial"]: x["role"] for x in cb.compute(conn)["items"]}
    assert m == {"M-VM": "vm", "M-SV": "phys_server", "M-PT": "mgmt_orphan"}


# ---------------------------------------------------------------- M2
def test_M2_人工指定代表筆_重複登記不升格(tmp_path):
    conn, _ = base._conn(tmp_path)
    cb.set_override(conn, "HW-VM", "phys_server", "審查重現", "admin")
    m = base._roles(conn)
    assert m["HW-VM"]["role"] == "phys_server" and m["HW-VM"]["override"]
    assert m["DYN-VM1"]["role"] == "duplicate"            # 同一台的第二筆仍是重複登記，不是變成第二台 VM
    assert len({x["machine_key"] for x in m.values() if x["role"] != "duplicate"}) == \
        len({x["machine_key"] for x in m.values()})


# ---------------------------------------------------------------- M3
def test_M3_同U只有一台_廠商不同不併(tmp_path):
    host = {"key": "h", "name": "SECSVR163-08", "model": "Lenovo x3550 M5", "u_start": 8, "u_end": 8, "os": "Windows Server 2016"}
    port = {"key": "p", "name": "SECOOB163-008", "model": "DELL", "u_start": 8, "u_end": 8, "os": "N/A"}
    assert rl.mgmt_pairs([host, port]) == {}              # 不併 → rack_boxes 會列成找不到本體
    same = {**port, "model": "Lenovo XCC"}
    assert rl.mgmt_pairs([host, same])["p"]["host"] == "h"
    unknown = {**port, "model": None, "name": "SECOOB163-X"}
    assert rl.mgmt_pairs([host, unknown]) == {}           # 同 U 規則要廠商看得出來而且相同
    bx = rl.rack_boxes([{**host, "state": "active"}, {**port, "state": "active"}],
                       {"merged": [], "ignore": [], "suspect": []})
    assert bx["category"]["p"] == "orphan"


# ---------------------------------------------------------------- L1
def test_L1_同一個箱子的代表挑法固定(tmp_path):
    def dev(k):
        return {"key": k, "name": "same", "model": "IBM S922", "u_start": 1, "u_end": 4, "machine_type": "power",
                "hw_serials": ["X1"], "state": "active", "w": None, "asset_serial": k}
    devs = [dev("k2"), dev("k1"), dev("k3")]
    heads = {overlap_queue.merge_plan(list(o), "r", "k", acts=[])["merged"][0]["head"]
             for o in (devs, devs[::-1], [devs[1], devs[2], devs[0]])}
    assert heads == {"k1"}


# ---------------------------------------------------------------- L2
def test_L2_一體機DB節點與疑似登記錯誤的伺服器算主機_儲存cell不算(tmp_path):
    conn = _blank(tmp_path)
    for s, h in (("E-DB", "bqtx-db01"), ("E-CL", "bqtx-cel01")):
        _hw(conn, s, h, f"10.99.2.{len(s)}{h[-1]}", rack="M20/1-10U", model="Oracle Exadata X10M", os="Oracle Linux 8")
    conn.commit()
    keys = [m["key"] for m in rl.machines(conn) if m["asset_serial"] in ("E-DB", "E-CL")]
    room = rl.machines(conn)[0]["room"]
    overlap_queue.decide(conn, room, "M20", keys, "merge", "tester")
    out = cb.compute(conn)
    it = {x["asset_serial"]: x for x in out["items"]}
    assert it["E-DB"]["role"] == it["E-CL"]["role"] == "appliance"
    assert it["E-DB"]["os_instance"] and not it["E-CL"]["os_instance"]
    hosts = next(t for t in out["summary"]["trial"] if t["key"] == "hosts")
    assert next(p for p in hosts["parts"] if p["role"] == "appliance")["n"] == 1


# ---------------------------------------------------------------- L3
def test_L3_失效的人工指定不套用也不500_寫入要過關卡(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from routers import countbridge as rcb

    conn, _ = base._conn(tmp_path)
    cb.ensure_tables(conn)
    conn.execute("INSERT INTO count_role_override (asset_serial, role, note, updated_by, updated_at) "
                 "VALUES ('HW-LONE', 'gone_role', '舊版角色', 'admin', ?)", (_ago(3),))
    conn.commit()
    out = cb.compute(conn)
    it = {x["asset_serial"]: x for x in out["items"]}
    assert it["HW-LONE"]["role"] == "phys_server" and not it["HW-LONE"]["override"]
    assert [x["asset_serial"] for x in out["invalid_overrides"]] == ["HW-LONE"]
    req = type("R", (), {"headers": {}, "client": type("C", (), {"host": "10.99.0.9"})()})()
    monkeypatch.setattr(rcb, "_can_edit_nav", lambda s, c: True)
    import write_guard

    def need(c, r, password=None):
        raise write_guard.NeedPassword("要管理密碼")
    monkeypatch.setattr(write_guard, "check", need)
    with pytest.raises(HTTPException) as e:
        rcb.count_bridge_override_endpoint(rcb.CountRoleOverrideBody(asset_serial="HW-LONE", role="unknown"),
                                           req, None, conn)
    assert e.value.status_code == 428
    body = rcb.count_bridge_endpoint(None, conn)
    assert body["invalid_overrides"][0]["role"] == "gone_role"


# ---------------------------------------------------------------- L4
def test_L4_復原的檢查跟寫入在同一個交易(tmp_path):
    conn = _blank(tmp_path)
    for s, h, r in (("A-1", "app01", "B10/20U"), ("A-2", "app02", "B11/10U")):
        _hw(conn, s, h, f"10.99.6.{s[-1]}", rack=r, model="DELL R750")
    conn.commit()
    k = next(x["key"] for x in rl.machines(conn) if x["asset_serial"] == "A-2")
    mv = rl.move_box(conn, k, "甲機房", "B11", 5, 1, "一", "alice")
    rl.move_box(conn, k, "甲機房", "B11", 7, 1, "二", "bob")
    with pytest.raises(rl.MoveChanged):
        rl.undo_move(conn, mv["move_id"], "alice")
    assert not conn.in_transaction                        # 被擋下時鎖要放掉
    seen = []
    conn.set_trace_callback(seen.append)
    rl.undo_move(conn, mv["move_id"], "alice", force=True)
    conn.set_trace_callback(None)
    b = next(i for i, s in enumerate(seen) if s.strip().upper().startswith("BEGIN IMMEDIATE"))
    chk = next(i for i, s in enumerate(seen) if "FROM rack_move_log WHERE id" in s)
    wr = next(i for i, s in enumerate(seen) if s.startswith("UPDATE rack_move_log"))
    assert b < chk < wr                                   # 先拿寫鎖 → 再檢查 → 再寫


# ---------------------------------------------------------------- 終審：強訊號＋is_vm=1 仍是管理口（對帳橋那一側）
def test_終審_ilom尾巴_is_vm標1_仍是管理口(tmp_path):
    conn = _blank(tmp_path)
    _hw(conn, "V-P", "x-ilom", "10.99.5.2", is_vm="1", os="Oracle Linux 8")          # 沒機櫃：走對帳橋自己的判斷
    _hw(conn, "V-W", "y-oob", "10.99.5.3", is_vm="1", os="Windows Server 2022",       # 只有弱訊號＋VM → 是 VM
        asset_name="硬體遠端管理")
    conn.commit()
    it = {x["asset_serial"]: x["role"] for x in cb.compute(conn)["items"]}
    assert it == {"V-P": "mgmt_orphan", "V-W": "vm"}


# ---------------------------------------------------------------- 再審：x-ilom（OS 寫 Oracle Linux）要併進 x
def test_再審_ilom尾巴_OS是OracleLinux_照樣併進本體(tmp_path):
    conn = _blank(tmp_path)
    _hw(conn, "O-H", "x", "10.99.3.1", rack="B3/29-34U", model="ODA X7-2 HA", os="Oracle Linux 7")
    _hw(conn, "O-P", "x-ilom", "10.99.3.2", rack="B3/29-34U", model="ODA X7-2 HA", os="Oracle Linux 7")
    conn.commit()
    it = {x["asset_serial"]: x for x in cb.compute(conn)["items"]}
    assert it["O-P"]["role"] == "mgmt_attached" and it["O-P"]["host_serial"] == "O-H"
    assert it["O-H"]["role"] == "phys_server"
    t = next(t for r in rl.datacenter(conn)["rooms"] for t in r["racks"] if t["rack"] == "B3")
    assert t["device_count"] == 1 and t["breakdown"]["port"] == 1
