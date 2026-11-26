"""主機履歷頁 summary／machine 加速（2.070.x）：只改「怎麼取資料」，每一欄數字跟改之前完全相同（D3）。

改了什麼（每一條都有一個「新＝舊」的比對）：
1. summary 只要每段筆數：不再算完整資產頁（RVTools 證人、VIP、MAC 校正…），改呼叫 asset_detail 自己也用的
   asset_detail_core（同一份程式）。→ 跟「先算完整資產頁、再取筆數」逐欄相等。
2. 帳號段 list_accounts(context=False)：不算合規脈絡欄／OS 類別（只加欄位、不增減列）。→ 列、去重、筆數相等。
3. 機櫃：v2_views._rack_info 與 summary 的三段改用 machines_readonly_full（4-5 機櫃立面與對帳橋本來就用它），
   同一個 Ctx 只取一次。→ 跟可寫版 machines() 逐欄相等；machine() 一次請求只算一遍全站機器清單（原本兩遍）。
4. existence.for_serial：請求範圍內只還原一次全機隊結果、建序號索引。→ 每一筆序號跟逐台找第一筆相等，回的是拷貝。
5. onboard_eligibility.esxi_host_names：請求範圍內只掃一次。→ 結果相等、回拷貝。
效能回歸用「呼叫次數」守（不量秒數，慢機器不會假紅）。全部假資料（10.99.x）。"""
import copy
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import account_inventory  # noqa: E402
import collected_os  # noqa: E402
import db  # noqa: E402
import existence  # noqa: E402
import fleet_cache  # noqa: E402
import machine_identity  # noqa: E402
import onboard_eligibility as oe  # noqa: E402
import oncall  # noqa: E402
import rack_layout  # noqa: E402
import v2_sections as vs  # noqa: E402
import v2_views  # noqa: E402
from routers import assets as ra  # noqa: E402


def _hw(conn, serial, hostname, ip, **kw):
    fields = {"asset_serial": serial, "hostname": hostname, "ip": ip, "physical_location": "01_甲機房", "rack_no": "B2/10-11U",
              "device_model": "Dell R750", "os": "Red Hat Enterprise Linux 9", "api_id": "N-901", "asset_status": "使用中",
              "asset_name": f"系統-{serial}", "asset_purpose": "測試"}
    fields.update(kw)
    db.insert_hardware(conn, **fields)


@pytest.fixture(params=["hostname", "legacy"])
def conn(tmp_path, request):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    c.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('machine_key_mode', ?)", (request.param,))
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 實體機（機櫃上）＋同名多 IP（主機名口徑下是同一台）＋收集成功
    _hw(c, "P-1", "phys01", "10.99.1.1", rack_no="B2/20-21U", collect_ok=1, collect_checked_at=now)
    _hw(c, "P-2", "phys01", "10.99.1.2", rack_no="B2/20-21U", api_id="N-902")
    # 同一個箱子的 LPAR（同櫃同 U、Power 機型、AIX）
    _hw(c, "L-1", "lpar01", "10.99.2.1", rack_no="B2/30-33U", device_model="IBM 9105-42A", os="AIX 7.2")
    _hw(c, "L-2", "lpar02", "10.99.2.2", rack_no="B2/30-33U", device_model="IBM 9105-42A", os="AIX 7.3")
    _hw(c, "L-3", "vios01", "10.99.2.3", rack_no="B2/30-33U", device_model="IBM 9105-42A", os="VIOS 3.1")
    # 同櫃其他設備、網路設備
    _hw(c, "S-1", "sw01", "10.99.3.1", rack_no="B2/40U", device_model="Cisco C9300", os="")
    # 虛擬機（不在機櫃）、ESXi（在機櫃）
    _hw(c, "V-1", "vm01", "10.99.4.1", rack_no="VM", device_model="(VM)", is_vm="Y", api_id="N-901")
    _hw(c, "E-1", "esxi01", "10.99.5.1", rack_no="B2/12-13U", os="VMware ESXi 7.0", device_model="Dell R650")
    # 退役＋同名現役（退役＝備查，不跟現役合併）
    _hw(c, "R-1", "old01", "10.99.6.1", asset_status="報廢", rack_no="B2/5U")
    _hw(c, "R-2", "old01", "10.99.6.1")
    # 沒有主機名、沒有 IP、帳外
    _hw(c, "C-1", None, None, rack_no="")
    _hw(c, "DYN-10.99.8.1", "dyn01", "10.99.8.1", rack_no="")
    # 收集到的東西：帳號（兩個 IP 各收一次＝同一台去重）、套件、網卡（MAC 跟 CIA 不同＝有 MAC 校正）、HBA、規格
    for ip, sn in (("10.99.1.1", "P-1"), ("10.99.1.2", "P-2")):
        for u, uid in (("root", 0), ("app", 1001), (f"only_{ip[-1]}", 2000)):
            c.execute("INSERT INTO host_account (ip, asset_serial, username, uid, shell, can_login, kind, is_sudoer, last_seen) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", (ip, sn, u, uid, "/bin/bash", 1, "human", 1 if uid == 0 else 0, now))
    for n in ("bash", "openssl", "glibc"):
        c.execute("INSERT INTO host_package (ip, asset_serial, name, version, arch, source, last_seen) VALUES (?,?,?,?,?,?,?)",
                  ("10.99.1.1", "P-1", n, "1.0", "x86_64", "ssh_rpm", now))
    c.execute("UPDATE hardware SET mac = 'aa:bb:cc:00:00:01' WHERE asset_serial = 'P-1'")
    c.execute("INSERT INTO asset_nic (asset_serial, nic_name, mac, ip, speed_cur, speed_max) VALUES (?,?,?,?,?,?)",
              ("P-1", "eth0", "aa:bb:cc:00:00:99", "10.99.1.1", 1000, 10000))
    c.execute("INSERT INTO host_spec (asset_serial, collected_at, storage_json) VALUES (?,?,?)",
              ("P-1", now, json.dumps({"by": "vg", "items": [{"name": "rootvg", "size_gb": 100, "free_gb": 20}]})))
    # vCenter 匯入：esxi01 是 ESXi 主機、vm01 在 vCenter 清單裡
    c.execute("INSERT INTO source_record (source, source_key, payload, resolved_hardware_id) VALUES (?,?,?,?)",
              ("vcenter", "vm-1", json.dumps({"hostname": "vm01", "vm_name": "vm01", "ip": "10.99.4.1", "esxi_host": "esxi01"}),
               c.execute("SELECT id FROM hardware WHERE asset_serial = 'V-1'").fetchone()[0]))
    for ip, hn in (("10.99.1.1", "phys01"), ("10.99.4.1", "vm01")):
        c.execute("INSERT INTO scan_history (scan_time, ip, hostname, scan_ok, mac) VALUES (?, ?, ?, 1, ?)",
                  (now, ip, hn, "00:50:56:aa:bb:cc" if hn == "vm01" else None))
    c.commit()
    machine_identity.refresh(c)
    # 每個程序第一次讀型號規格會補種子、有些段第一次讀會建表（寫庫）；先走一遍，再換一條沒寫過的新連線——
    # 正式機每個請求都是新連線，連線自己寫過時請求範圍快取整條不用（fleet_cache 的保守規則），測不出「只算一次」
    rack_layout.machines(c)
    k0 = sorted(oncall._groups(c)[0])[0]
    vs.all_sections(c, k0)
    v2_views.machine(c, k0)
    c.commit()
    c.close()
    fleet_cache.clear()
    c = db.get_connection(p)
    assert c.total_changes == 0
    yield c
    c.close()
    fleet_cache.clear()


def _keys(conn):
    groups, _ = oncall._groups(conn)
    return sorted(groups)


def _old_summary(conn, key, monkeypatch):
    """改之前的算法：先算完整資產頁（asset_detail）、帳號帶合規脈絡欄、機櫃用可寫版 machines()。"""
    real_list = account_inventory.list_accounts
    with monkeypatch.context() as m:
        m.setattr(account_inventory, "list_accounts", lambda *a, **k: real_list(*a, **{**k, "context": True}))
        m.setattr(vs.Ctx, "rack_machines", lambda self: rack_layout.machines(self.conn))
        ctx = vs.Ctx(conn, key)
        ctx.detail()                    # counts_only 還是 False：完整資產頁
        return vs.summary(conn, key, ctx=ctx)


def test_summary_精簡路線_逐欄等於改之前(conn, monkeypatch):
    keys = _keys(conn)
    assert any(k.startswith("ret:") for k in keys)
    for k in keys:
        old = _old_summary(conn, k, monkeypatch)
        with fleet_cache.request_scope(conn):
            new = vs.summary(conn, k)
        assert new == old, k
    # 案例真的有走到：主機名口徑下 phys01 兩筆是同一台——帳號 6（兩個 IP 各 3 個，這段依 IP＋帳號列）、套件 3、
    # 網卡 2（收到的 1＋CIA 另一個 IP）、儲存 1；舊口徑下 10.99.1.1 那一台自己：帳號 3、網卡 1。同櫃都有其他箱子
    with fleet_cache.request_scope(conn):
        s = {x["key"]: x for x in vs.summary(conn, next(k for k in keys if "phys01" in k or k.startswith("sn:P-1")))["sections"]}
    want = (6, 3, 2, 1) if machine_identity.mode() == machine_identity.MODE_HOSTNAME else (3, 3, 1, 1)
    assert (s["accounts"]["count"], s["software"]["count"], s["nics"]["count"], s["disks"]["count"]) == want
    assert s["same_rack"]["count"] and s["same_rack"]["count"] >= 1


def test_逐段載入_機櫃改唯讀版_逐欄等於可寫版(conn, monkeypatch):
    for k in _keys(conn):
        new = vs.all_sections(conn, k)
        with monkeypatch.context() as m:
            m.setattr(vs.Ctx, "rack_machines", lambda self: rack_layout.machines(self.conn))
            old = vs.all_sections(conn, k)
        assert new == old, k


def test_machine端點_機櫃改唯讀版_逐欄等於可寫版(conn, monkeypatch):
    for k in _keys(conn):
        with fleet_cache.request_scope(conn):
            new = v2_views.machine(conn, k)
        fleet_cache.clear()
        with monkeypatch.context() as m:
            m.setattr(rack_layout, "machines_readonly_full", lambda c: rack_layout.machines(c))
            old = v2_views.machine(conn, k)
        fleet_cache.clear()
        assert new == old, k


def test_唯讀版機器清單_內容等於可寫版(conn):
    assert rack_layout.machines_readonly_full(conn) == rack_layout.machines(conn)


def test_帳號_不算脈絡欄時_列與去重相同(conn):
    for s in ("P-1", "P-2", "V-1", "C-1"):
        full = account_inventory.list_accounts(conn, asset_serial=s)
        lite = account_inventory.list_accounts(conn, asset_serial=s, context=False)
        cols = ("ip", "username", "uid", "kind", "is_sudoer", "can_login", "last_login", "pw_status", "shell", "seen_ips")
        assert [{c: r.get(c) for c in cols} for r in lite] == [{c: r.get(c) for c in cols} for r in full], s
    # 有 OS 類別篩選時一律照算（不能因為 context=False 把篩選弄壞）
    a = account_inventory.list_accounts(conn, asset_serial="P-1", os_bucket="linux")
    b = account_inventory.list_accounts(conn, asset_serial="P-1", os_bucket="linux", context=False)
    assert [r["username"] for r in a] == [r["username"] for r in b]


def test_asset_detail_core_跟資產頁同一份(conn):
    import manage_state as ms
    for s in [r[0] for r in conn.execute("SELECT asset_serial FROM hardware")]:
        d = ra.asset_detail(s, None, conn)
        hw = conn.execute("SELECT * FROM hardware WHERE asset_serial = ?", (s,)).fetchone()
        core = ra.asset_detail_core(conn, s, hw, ms.same_base_key_rows(conn, s))
        for k in ("personnel", "software", "packages", "history"):
            assert [dict(r) for r in core[k]] == d[k], (s, k)
        for k in ("onboard_history", "health", "host_spec", "nics", "hbas"):
            assert core[k] == d[k], (s, k)


def test_existence_for_serial_範圍內外相同且回拷貝(conn):
    serials = [r[0] for r in conn.execute("SELECT asset_serial FROM hardware")] + ["不存在"]
    outside = {s: existence.for_serial(conn, s) for s in serials}
    # 舊寫法：逐台找第一筆
    ref = {s: next((m for m in existence.compute(conn)["machines"] if s in m["serials"]), None) for s in serials}
    assert outside == ref
    with fleet_cache.request_scope(conn):
        inside = {s: existence.for_serial(conn, s) for s in serials}
        inside["P-1"]["level"] = "被呼叫端改掉"
        assert existence.for_serial(conn, "P-1")["level"] == ref["P-1"]["level"]
    inside["P-1"]["level"] = ref["P-1"]["level"]
    assert inside == ref


def test_esxi_host_names_範圍內只掃一次且回拷貝(conn, monkeypatch):
    ref = oe._esxi_host_names(conn)
    assert ref                                  # 案例有 ESXi 主機名
    calls = []
    real = oe._esxi_host_names
    monkeypatch.setattr(oe, "_esxi_host_names", lambda c: calls.append(1) or real(c))
    with fleet_cache.request_scope(conn):
        a = oe.esxi_host_names(conn)
        a.add("被呼叫端加的")
        b = oe.esxi_host_names(conn)
    assert len(calls) == 1 and b == ref
    oe.esxi_host_names(conn)
    assert len(calls) == 2                      # 沒有請求範圍：照舊每次重掃


def _count(monkeypatch, mod, name):
    n = {"n": 0}
    real = getattr(mod, name)

    def w(*a, **k):
        n["n"] += 1
        return real(*a, **k)
    monkeypatch.setattr(mod, name, w)
    return n


def test_效能回歸_summary不算完整資產頁與整機隊脈絡(conn, monkeypatch):
    k = next(k for k in _keys(conn) if "phys01" in k or k.startswith("sn:P-1"))
    detail = _count(monkeypatch, ra, "asset_detail")
    rv = _count(monkeypatch, collected_os, "rvtools_map")
    ctxn = _count(monkeypatch, account_inventory, "_add_context")
    mach = _count(monkeypatch, rack_layout, "_machines")
    fleet_cache.clear()
    with fleet_cache.request_scope(conn):
        vs.summary(conn, k)
    assert (detail["n"], ctxn["n"]) == (0, 0)
    assert rv["n"] <= 1                         # 只剩實測 OS（measured_for_rows，範圍內備忘）那一次；資產頁自己那次不算了
    assert mach["n"] == 1                       # 全站機器清單一次請求只算一遍


def test_效能回歸_machine端點全站機器清單只算一遍(conn, monkeypatch):
    k = next(k for k in _keys(conn) if "phys01" in k or k.startswith("sn:P-1"))
    mach = _count(monkeypatch, rack_layout, "_machines")
    esx = _count(monkeypatch, oe, "_esxi_host_names")
    fleet_cache.clear()
    with fleet_cache.request_scope(conn):
        out = v2_views.machine(conn, k)
    assert out["mode"] == "machine"
    assert mach["n"] == 1 and esx["n"] <= 1


def test_summary與逐段載入_共用Ctx時仍是完整路線(conn):
    """verify_numbers 的用法：先 all_sections 再 summary（同一個 Ctx）——summary 用已經算好的完整資產頁，不另取精簡版。"""
    k = next(k for k in _keys(conn) if "phys01" in k or k.startswith("sn:P-1"))
    ctx = vs.Ctx(conn, k)
    secs = vs.all_sections(conn, k, ctx=ctx)
    s = vs.summary(conn, k, ctx=ctx)
    assert {x["key"]: x["count"] for x in s["sections"]} == {x["key"]: x["count"] for x in secs}
    assert getattr(ctx, "_detail_core", None) is None
    assert copy.deepcopy(s) == vs.summary(conn, k)


def test_machine_key備忘_等於不備忘_口徑一換就作廢(conn, monkeypatch):
    import system_stats as ss
    rows = [dict(r) for r in conn.execute("SELECT hostname, ip, asset_serial FROM hardware")]
    rows += [{"hostname": "ap02.sit.example.com.tw", "ip": "10.99.9.1", "asset_serial": "Z-1"},
             {"hostname": "sw-254.254-1", "ip": "10.99.9.2", "asset_serial": "Z-2"},
             {"hostname": "localhost", "ip": "10.99.9.3", "asset_serial": "Z-3"},
             {"hostname": "x", "ip": "0.0.0.0", "asset_serial": "Z-4"}]
    for _ in range(2):                          # 第二輪全部走備忘
        for r in rows:
            assert ss.machine_key(r["hostname"], r["ip"], r["asset_serial"]) == \
                ss._machine_key_uncached(r["hostname"], r["ip"], r["asset_serial"]), r
    # 實測主機名校正換了（新物件）：備忘不可以沿用舊口徑的鍵
    before = ss.machine_key("phys01", "10.99.1.1", "P-1")
    monkeypatch.setitem(machine_identity._STATE, "host_fix", {"P-1": "renamed01"})
    after = ss.machine_key("phys01", "10.99.1.1", "P-1")
    assert after == ss._machine_key_uncached("phys01", "10.99.1.1", "P-1")
    assert after != before
    # 口徑換了：同上
    other = machine_identity.MODE_LEGACY if machine_identity.mode() == machine_identity.MODE_HOSTNAME else machine_identity.MODE_HOSTNAME
    monkeypatch.setitem(machine_identity._STATE, "mode", other)
    assert ss.machine_key("phys01", "10.99.1.1", "P-1") == ss._machine_key_uncached("phys01", "10.99.1.1", "P-1")


def test_refresh內容沒變_沿用同一個校正物件(conn):
    machine_identity.refresh(conn)
    a = machine_identity._STATE["host_fix"]
    machine_identity.refresh(conn)
    assert machine_identity._STATE["host_fix"] is a
