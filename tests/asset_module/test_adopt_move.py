"""/adopt 每一列手動搬到別的頁簽（2026-10-16）。

使用者在公司機 1.428.0：「我還是沒看到可以移動到其他組別的方式」。
實例：`SVR019-203` 看起來是一般伺服器，但資產登記的「作業系統」欄寫「儲存設備」，
於是被歸到儲存設備頁簽——自動分類一定有錯的時候，人要能自己改。

搬移沿用 2026-09-18 就做好的 `os_override`（os_type_override 表），不另做第二套儲存。

這支釘住三件事（使用者驗收條目）：
  (a) 人工搬移後 `dispatch_tab` 回新類別
  (b) 重新收集不會被蓋回去（run_dispatch 與 latest_run 都要）
  (c) 搬到 SSH **不會**讓 `batch_onboard_service` 把它當可納管
      ——安全邊界：往 SSH 搬只影響顯示與試連，不可以讓系統自動去那台建本機帳號

外加：頁簽 ↔ OS 大類的對照要雙向一致（搬去又讀回來，不可以跑到別的頁簽）。
"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import batch_onboard_service  # noqa: E402
import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402
import os_override  # noqa: E402

ADOPT_VUE = ROOT / "APP" / "asset-module" / "frontend" / "pages" / "adopt.vue"

# 使用者的實例：一般伺服器，但資產登記的作業系統欄寫「儲存設備」→ 被歸到儲存設備頁簽。
# IP 用 10.99.x（規矩：測試不得出現公司內網位址）
SVR = ("10.99.19.203", "SVR019-203", "儲存設備")
#: 真正的 NAS，用來驗反方向（Linux 誤判成伺服器 → 人工搬去儲存設備）
NAS = ("10.99.19.55", "NAS019-055", "Rocky Linux 9")


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn, ip, hostname, os_text, environment="測試"):
    db.insert_hardware(conn, asset_serial=f"S-{ip}", ip=ip, os=os_text, hostname=hostname,
                       environment=environment, asset_status="使用中")


def _run(conn, ips):
    """跑一輪收集：全部都開 22（route=ssh），收集帳號試連回「通」。"""
    return cd.run_dispatch(conn, "\n".join(ips), prober=lambda ip: [22],
                           ssh_prober=lambda ip: (True, None),
                           winrm_runner=lambda *a, **k: (False, "no cred"))


def _scanned(conn, ip, ports="22"):
    """批次納管的目標是從「最近一次掃描有掃到、活著」來的（expand_targets），
    不是 /adopt 的收集結果表——所以這兩支測試要另外餵 scan_history。"""
    conn.execute("INSERT INTO scan_history (ip, scan_ok, open_ports, scan_time) "
                 "VALUES (?, 1, ?, '2026-10-16 09:00:00')", (ip, ports))
    conn.commit()


def _move(conn, ip, hostname, tab, reason="人工指定"):
    """照 API 的做法搬：翻成 OS 大類後寫進 os_override。"""
    os_override.set_override(conn, hostname, ip, f"S-{ip}",
                             cd.os_type_for_tab(tab), None, reason, "tester")


# ---------- 對照表本身 ----------

def test_頁簽與OS大類的對照雙向一致():
    """搬到頁簽 X → 寫進 os_type → 讀回來一定還是 X。不然搬一次就跑到別頁。"""
    for tab in cd.MOVABLE_TABS:
        os_type = cd.os_type_for_tab(tab)
        assert os_type, f"{tab} 沒有對應的 OS 大類"
        assert cd.tab_for_os_type(os_type) == tab, f"{tab} → {os_type} → {cd.tab_for_os_type(os_type)}"


def test_可搬的類別都在os_override的合法選項裡():
    """寫進去會被 set_override 的白名單擋掉的話，搬移當場就會 400。"""
    allowed = set(os_override.choices())
    for tab in cd.MOVABLE_TABS:
        assert cd.os_type_for_tab(tab) in allowed, tab


def test_連線狀態的兩個頁簽不給搬():
    """「待裝 Agent」「只能匯入」是連不連得上，不是機器的類別，不可以當搬移目的地。"""
    assert cd.os_type_for_tab(cd.TAB_AGENT) is None
    assert cd.os_type_for_tab(cd.TAB_IMPORT) is None
    assert cd.TAB_AGENT not in cd.MOVABLE_TABS
    assert cd.TAB_IMPORT not in cd.MOVABLE_TABS


def test_搬移選單帶得出標籤與納不納管():
    items = {c["tab"]: c for c in cd.move_choices()}
    assert set(items) == set(cd.MOVABLE_TABS)
    assert items[cd.TAB_SSH]["onboardable"] is True
    assert items[cd.TAB_STORAGE]["onboardable"] is False
    assert items[cd.TAB_STORAGE]["label"] == "儲存設備"


# ---------- (a) 搬完 dispatch_tab 回新類別 ----------

def test_a_人工搬移後dispatch_tab回新類別():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        ip, host, os_text = SVR
        _seed(conn, ip, host, os_text)
        row = {"ip": ip, "hostname": host, "asset_serial": f"S-{ip}",
               "status": cd.STATUS_NOT_ONBOARDABLE, "not_onboardable_kind": "storage",
               "route": "ssh"}
        # 沒有指定時：照機器判的，落在儲存設備
        assert cd.dispatch_tab(row) == cd.TAB_STORAGE
        assert cd.dispatch_tab(row, os_override.active_map(conn)) == cd.TAB_STORAGE
        # 人工搬到 SSH 收集
        _move(conn, ip, host, cd.TAB_SSH, "這台是一般伺服器，OS 欄登記錯了")
        assert cd.dispatch_tab(row, os_override.active_map(conn)) == cd.TAB_SSH
        # 反方向：Linux 搬去儲存設備也要生效
        nip, nhost, nos = NAS
        _seed(conn, nip, nhost, nos)
        nrow = {"ip": nip, "hostname": nhost, "asset_serial": f"S-{nip}",
                "status": cd.STATUS_NEEDS_CREDENTIAL, "route": "ssh"}
        assert cd.dispatch_tab(nrow) == cd.TAB_SSH
        _move(conn, nip, nhost, cd.TAB_STORAGE, "這台是 NAS")
        assert cd.dispatch_tab(nrow, os_override.active_map(conn)) == cd.TAB_STORAGE
        conn.close()


def test_a_恢復自動後回機器判的類別():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        ip, host, os_text = SVR
        _seed(conn, ip, host, os_text)
        row = {"ip": ip, "hostname": host, "asset_serial": f"S-{ip}",
               "status": cd.STATUS_NOT_ONBOARDABLE, "not_onboardable_kind": "storage",
               "route": "ssh"}
        _move(conn, ip, host, cd.TAB_SSH)
        assert cd.dispatch_tab(row, os_override.active_map(conn)) == cd.TAB_SSH
        # 取消是軟刪除：紀錄留著，但不再套用
        os_override.clear(conn, host, ip, f"S-{ip}", "tester")
        assert cd.dispatch_tab(row, os_override.active_map(conn)) == cd.TAB_STORAGE
        assert os_override.list_active(conn) == []
        conn.close()


def test_a_主機名對不上就不套用():
    """IP 被回收給另一台機器時，舊指定要自動失效（os_override 的認台規則）。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        ip, host, os_text = SVR
        _seed(conn, ip, host, os_text)
        _move(conn, ip, host, cd.TAB_SSH)
        ov = os_override.active_map(conn)
        other = {"ip": ip, "hostname": "另一台-999", "asset_serial": "S-OTHER",
                 "status": cd.STATUS_NOT_ONBOARDABLE, "not_onboardable_kind": "storage",
                 "route": "ssh"}
        assert cd.dispatch_tab(other, ov) == cd.TAB_STORAGE
        conn.close()


# ---------- (b) 重新收集不會被蓋回去 ----------

def test_b_重新收集不會蓋回去_而且會真的去試連():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        ip, host, os_text = SVR
        _seed(conn, ip, host, os_text)

        out = _run(conn, [ip])
        r = out["results"][0]
        assert r["tab"] == cd.TAB_STORAGE
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE

        _move(conn, ip, host, cd.TAB_SSH, "一般伺服器，OS 欄登記錯")

        # 重新收集：頁簽要是 SSH，而且**真的去試連過**（所以 status 不再是不納管）
        out2 = _run(conn, [ip])
        r2 = out2["results"][0]
        assert r2["tab"] == cd.TAB_SSH, "重新收集把人工指定蓋回去了"
        assert r2["status"] != cd.STATUS_NOT_ONBOARDABLE
        assert r2["status"] == cd.STATUS_COLLECTED
        assert r2["os_override"]["by"] == "tester"
        assert r2["os_override"]["tab"] == cd.TAB_SSH
        # 重新整理頁面（latest_run）也一樣
        again = cd.latest_run(conn)
        assert again["results"][0]["tab"] == cd.TAB_SSH
        assert again["by_tab"][cd.TAB_SSH] == 1
        assert again["by_tab"][cd.TAB_STORAGE] == 0
        conn.close()


def test_b_往不納管搬_重新收集也不會被判回可收集():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        nip, nhost, nos = NAS
        _seed(conn, nip, nhost, nos)
        assert _run(conn, [nip])["results"][0]["tab"] == cd.TAB_SSH

        _move(conn, nip, nhost, cd.TAB_STORAGE, "這台其實是 NAS")
        out = _run(conn, [nip])
        r = out["results"][0]
        assert r["tab"] == cd.TAB_STORAGE
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE
        assert r["not_onboardable_kind"] == "storage"
        # 留痕要看得見：誰／何時／原因都在這一列上
        assert "人工指定" in r["message"]
        assert "tester" in r["message"]
        assert r["os_override"]["reason"] == "這台其實是 NAS"
        # 不納管小計跟頁簽講同一件事
        assert out["not_onboardable"] == 1
        assert out["by_tab"][cd.TAB_STORAGE] == 1
        conn.close()


def test_b_登記寫RHEL的OCP節點搬到OCP_不試連_也進3_7的OCP桶():
    """2026-10-21 使用者：「這裡怎麼沒有 OCP 分類」「OCP 的 OS 也是 rhel 要小心」。
    登記只寫 RHEL 的 OCP 節點會落在 SSH 收集；人工搬到 OCP 頁簽後要被擋下（不試連、不納管），
    而且帳號儀表板的 OCP 桶用的是同一份人工指定。"""
    import account_inventory

    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        # 名稱刻意不帶 ocp：帶 ocp 的 2026-10-22 起自動歸 OCP（onboard_eligibility.is_ocp_name）
        ip, host = "10.99.20.11", "worker-w11"
        _seed(conn, ip, host, "Red Hat Enterprise Linux 8")
        assert _run(conn, [ip])["results"][0]["tab"] == cd.TAB_SSH

        _move(conn, ip, host, cd.TAB_OCP, "這台是 OCP worker")
        out = _run(conn, [ip])
        r = out["results"][0]
        assert r["tab"] == cd.TAB_OCP
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE
        assert r["not_onboardable_kind"] == "immutable"
        assert out["by_tab"][cd.TAB_OCP] == 1 and out["not_onboardable"] == 1
        assert ip in account_inventory.ocp_ips(conn)
        conn.close()


def test_b_舊run讀回來_頁簽搬走_結果跟著人工分類_講明還沒試連():
    """2026-10-22 使用者改規則：結果也跟著人工分類走（以前是「status 不假造」）。
    搬回可收集 → 依路線的待辦（SSH＝待佈身分，可一鍵納管）；message 講明還沒試連。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        ip, host, os_text = SVR
        _seed(conn, ip, host, os_text)
        _run(conn, [ip])
        _move(conn, ip, host, cd.TAB_SSH)

        out = cd.latest_run(conn)
        r = out["results"][0]
        assert r["tab"] == cd.TAB_SSH
        assert r["status"] == cd.STATUS_NEEDS_CREDENTIAL and not r.get("not_onboardable_kind")
        assert "還沒試連過" in r["message"] and "人工指定" in r["message"]
        conn.close()


def test_b_VMware搬回SSH_仍不轉待辦_不給一鍵納管():
    """VMware 天條（獨立審查 2026-10-22）：判成 ESXi 的，人工搬回可收集也維持另有納管方式。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        ip, host = "10.99.20.31", "esx-31"
        _seed(conn, ip, host, "VMware ESXi 8.0.3")
        _run(conn, [ip])
        _move(conn, ip, host, cd.TAB_SSH, "搬錯測試")
        r = cd.latest_run(conn)["results"][0]
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE, r
        assert "VMware 一律不建帳號" in r["message"]
        conn.close()


def test_b_搬到儲存_結果改另有納管方式():
    """2026-10-22 使用者：「19.210 是 storage，我歸到 storage，結果應該是『另有納管方式』」。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        ip, host = "10.99.20.21", "svr-210"
        _seed(conn, ip, host, "Red Hat Enterprise Linux 8")
        cd.run_dispatch(conn, ip, prober=lambda _ip: [22],
                        ssh_prober=lambda _ip: (False, "Permission denied (publickey)."))
        assert cd.latest_run(conn)["results"][0]["status"] == cd.STATUS_NEEDS_CREDENTIAL
        _move(conn, ip, host, cd.TAB_STORAGE, "其實是儲存設備")
        r = cd.latest_run(conn)["results"][0]
        assert r["tab"] == cd.TAB_STORAGE
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE and r["not_onboardable_kind"] == "storage"
        assert "人工指定" in r["message"]
        conn.close()


def test_b_人工指定的列不被舊run重判蓋回去():
    """`_rejudge_*` 是拿「現在的資產登記」重跑機器判定，而登記正是判錯的那一份。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        ip, host, os_text = SVR
        _seed(conn, ip, host, os_text)
        _run(conn, [ip])
        # 模擬舊 run：類別欄 NULL（1.422.0 以前沒存類別）
        conn.execute("UPDATE collect_dispatch_result SET not_onboardable_kind = NULL")
        conn.commit()
        _move(conn, ip, host, cd.TAB_SSH)
        assert cd.latest_run(conn)["results"][0]["tab"] == cd.TAB_SSH
        conn.close()


def test_b_頁簽互斥且加總仍等於全部():
    """搬移不可以破壞「各頁簽加總＝全部」這條鐵律（加總＝下鑽）。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        for ip, host, os_text in (SVR, NAS):
            _seed(conn, ip, host, os_text)
        _run(conn, [SVR[0], NAS[0]])
        _move(conn, SVR[0], SVR[1], cd.TAB_SSH)
        _move(conn, NAS[0], NAS[1], cd.TAB_STORAGE)
        for out in (_run(conn, [SVR[0], NAS[0]]), cd.latest_run(conn)):
            by_tab = out["by_tab"]
            assert set(by_tab) == set(cd.TAB_ORDER)
            assert sum(by_tab.values()) == out["total"] == len(out["results"])
            for t in cd.TAB_ORDER:
                assert by_tab[t] == sum(1 for r in out["results"] if r["tab"] == t), t
        conn.close()


# ---------- (c) 安全邊界：搬到 SSH 不等於可自動納管 ----------

def test_c_搬到SSH不會讓批次納管把它當可納管():
    """使用者拍板的安全邊界：往 SSH 搬只影響顯示與試連，
    **不可以**讓系統自動去那台建本機帳號。白名單讀的是資產登記的作業系統欄，
    完全不看 os_type_override。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        conn = _conn(tmp)
        ip, host, os_text = SVR
        _seed(conn, ip, host, os_text)
        # 先讓這個 IP 在掃描結果裡「活著、開 22」——不然 expand_targets 取不到它
        _scanned(conn, ip)
        _run(conn, [ip])

        before = batch_onboard_service.classify_targets(conn, [ip])
        _move(conn, ip, host, cd.TAB_SSH, "一般伺服器")
        after = batch_onboard_service.classify_targets(conn, [ip])

        # 搬移前後完全一樣：白名單不受人工搬移影響
        assert after == before
        # 而且這台本來就不在「會自動跑」那一堆裡（作業系統欄寫著儲存設備 → excluded）
        assert [h["ip"] for h in after["run"]] == []
        assert ip in [h["ip"] for h in after["excluded"]]
        conn.close()


def test_c_白名單完全不讀os_type_override這張表():
    """靜態釘住：batch_onboard_service 不得出現 os_override。
    行為測試會被未來的改動繞過（例如改讀別的欄位），這條直接擋在原始碼層。"""
    src = (ROOT / "APP" / "asset-module" / "backend" / "batch_onboard_service.py").read_text(
        encoding="utf-8")
    assert "os_override" not in src
    assert "os_type_override" not in src


def test_c_真正的Linux搬去儲存設備_白名單照舊能納管():
    """反方向也不可以越權：把 Linux 標成儲存設備是「顯示上不要它」，
    不是「把它從白名單踢掉」。白名單只看資產登記，所以結論不變。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        conn = _conn(tmp)
        nip, nhost, nos = NAS
        _seed(conn, nip, nhost, nos)
        _scanned(conn, nip)
        _run(conn, [nip])
        before = batch_onboard_service.classify_targets(conn, [nip])
        assert [h["ip"] for h in before["run"]] == [nip]
        _move(conn, nip, nhost, cd.TAB_STORAGE)
        assert batch_onboard_service.classify_targets(conn, [nip]) == before
        conn.close()


# ---------- 前端：入口與勾選要真的在 /adopt 這一頁 ----------

def test_前端_adopt每一列有改分類入口():
    """使用者原話：「我還是沒看到可以移動到其他組別的方式」——入口要在 /adopt 列上，
    不是只留在資產明細頁。"""
    src = ADOPT_VUE.read_text(encoding="utf-8")
    assert "/api/adopt/move-tab" in src, "adopt.vue 沒有呼叫搬移 API"
    assert "/api/adopt/move-choices" in src, "adopt.vue 沒有讀搬移選單"
    assert "改分類" in src, "adopt.vue 沒有每一列的「改分類」入口"
    # 人工指定要標示得出來（誰／何時／從哪類改到哪類）
    assert "os_override" in src, "adopt.vue 沒有顯示人工指定的標記"
    assert "人工指定" in src


def test_前端_不納管頁簽每列可勾且可全選可批次搬移():
    """使用者原話：「圖二不能勾選，不能全選」。四個不納管頁簽都要能逐列勾、表頭全選。"""
    src = ADOPT_VUE.read_text(encoding="utf-8")
    for name in ("canMove", "moveSelected", "allMovableSelected", "toggleAllMovable",
                 "toggleMove", "movableRows"):
        assert name in src, f"adopt.vue 缺 {name}"
    assert "批次搬移" in src
    # 搬移的勾選跟「批次試密碼」的勾選是兩組，不混用（兩個 Set 各自存在）
    assert "probeSelected" in src and "moveSelected" in src
    assert "moveSelected = new Set()" in src or "moveSelected.value = new Set()" in src


# ---------- 「那它走哪條路」：不納管 ≠ 不管（2026-10-16）----------
# 使用者：「網路設備／ESXi／儲存 這些都有它獨立納管方式」「不是靠 Linux 方式納管」。
# 修完不納管判定後，ESXi 落在 ESXi 頁簽了，但「走哪條路」還寫 Push Agent
#（route 是探測事實）、「下一步」寫「不納管」——兩個都在誤導。

def test_指路_各類別指到對的收集方式():
    assert cd.nok_guidance("esxi")["page"] == "/import"
    assert cd.nok_guidance("esxi")["how"] == "vCenter／RVTools 匯入"
    assert cd.nok_guidance("storage")["page"] == "/san"
    assert cd.nok_guidance("storage")["how"] == "SAN 收集"
    assert cd.nok_guidance("immutable")["page"] == "/ocp"
    # IBM i／Power 現在被 classify_os 歸進 appliance，要在那一桶裡再認出來
    g = cd.nok_guidance("appliance", "IBM i 7.4")
    assert g["kind"] == "ibmi" and g["page"] == "/hmc" and g["how"] == "HMC 收集"
    assert cd.nok_guidance("appliance", "OS/400 V7R4")["page"] == "/hmc"


def test_指路_網路設備目前不收_不假裝有路():
    """使用者 2026-10-16：「暫時無打算」。不可以編一條不存在的收集路徑出來。"""
    g = cd.nok_guidance("network")
    assert g["how"] is None and g["page"] is None
    assert "目前不收" in g["note"]
    # 認不出是什麼設備的也一樣，不給假路
    for kind in ("appliance", "banner"):
        assert cd.nok_guidance(kind, "SecureKi Harden")["how"] is None


def test_指路_只有不納管的列才有guide_而且不竄改route():
    """route 是探測事實——`by_route` 統計與前端 isWindowsOnSsh 都靠它，不可以改。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        nas = ("10.99.19.55", "NAS019-055", "儲存設備")
        linux = ("10.99.19.10", "app-10", "Rocky Linux 9")
        for ip, host, os_text in (nas, linux):
            _seed(conn, ip, host, os_text)
        out = _run(conn, [nas[0], linux[0]])
        rows = {r["ip"]: r for r in out["results"]}
        assert rows[nas[0]]["guide"]["page"] == "/san"
        assert rows[linux[0]].get("guide") is None
        # route 原值沒被動過：兩台都開 22 → 都還是 ssh
        assert rows[nas[0]]["route"] == "ssh" and rows[linux[0]]["route"] == "ssh"
        assert out["by_route"]["ssh"] == 2
        # 重新整理也一樣
        again = {r["ip"]: r for r in cd.latest_run(conn)["results"]}
        assert again[nas[0]]["guide"]["page"] == "/san"
        assert again[nas[0]]["route"] == "ssh"
        conn.close()


def test_指路_每個不納管類別都有一筆_不會漏掉變成空白():
    """漏一個類別，畫面上那幾台就只剩「不納管」三個字——那正是這次要修掉的。"""
    import onboard_eligibility as oe
    kinds = set(oe.NOT_ONBOARDABLE_LABEL) | {oe.KIND_BANNER, oe.KIND_NETWORK}
    for k in kinds:
        assert cd.nok_guidance(k) is not None, f"不納管類別 {k} 沒有指路"


def test_前端_不納管的列不再寫PushAgent與不納管():
    src = ADOPT_VUE.read_text(encoding="utf-8")
    assert "r.guide" in src, "adopt.vue 沒有用後端算的指路"
    assert "目前不收" in src
    # 選單編號要現查（使用者可在「選單編輯」改順序、
    # 功能開關也會讓編號位移），不可以寫死——寫死就會變成叫人去看一個不存在的編號
    assert "navNoOf" in src and "useVisibleNav" in src
    # 只看模板裡實際會顯示的字（排掉註解；註解裡講「預設 2-4」是說明，不是顯示）
    tpl = src[src.index("<template>"):]
    tpl = re.sub(r"<!--.*?-->", "", tpl, flags=re.S)
    for hard in ("2-4", "2-5"):
        assert hard not in tpl, f"adopt.vue 的模板寫死了選單編號 {hard}"


# ---------- 每一個頁簽都能批次搬移（2026-10-16 第二版）----------
# 使用者在公司機 1.429.0 勾了 4 台 Windows+OpenSSH 想一次搬到 WinRM 收集：
# 「**我沒辦法一次改**」。第一版把勾選的用途綁死在頁簽上，所以能動的三頁沒有批次搬移。
#
# 紅線（使用者拍板）：勾了一批想搬移，**絕對不能**按到「一次納管」把不該納管的送進去。

WIN_SSH = [("10.99.19.31", "WIN019-031"), ("10.99.19.32", "WIN019-032"),
           ("10.99.19.33", "WIN019-033"), ("10.99.19.34", "WIN019-034")]


def test_ssh頁勾多台一次搬到winrm_每台的頁簽都變成winrm():
    """使用者的實例：4 台裝了 OpenSSH 的 Windows 被排在 SSH 收集頁，要一次搬到 WinRM 收集。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        for ip, host in WIN_SSH:
            _seed(conn, ip, host, "Rocky Linux 9")   # 登記判成 Linux → 落在 SSH 收集頁
        ips = [ip for ip, _ in WIN_SSH]

        out = _run(conn, ips)
        assert {r["tab"] for r in out["results"]} == {cd.TAB_SSH}

        # 批次搬移＝同一支 API 逐台寫 os_override（前端把勾選的那幾列一起送進來）
        for ip, host in WIN_SSH:
            _move(conn, ip, host, cd.TAB_WINRM, "這台是裝了 OpenSSH 的 Windows")

        ov = os_override.active_map(conn)
        for ip, host in WIN_SSH:
            row = {"ip": ip, "hostname": host, "asset_serial": f"S-{ip}",
                   "status": cd.STATUS_NEEDS_CREDENTIAL, "route": "ssh"}
            assert cd.dispatch_tab(row, ov) == cd.TAB_WINRM, ip

        # 重新收集與重新整理都要留在 WinRM 頁，而且四台全部都在
        for again in (_run(conn, ips), cd.latest_run(conn)):
            assert again["by_tab"][cd.TAB_WINRM] == 4
            assert again["by_tab"][cd.TAB_SSH] == 0
            assert {r["tab"] for r in again["results"]} == {cd.TAB_WINRM}
        conn.close()


def test_紅線_整批搬到winrm後_批次納管的分類一台都沒變():
    """同一批機器在 `batch_onboard_service` 的分類**不因搬移而改變**。

    這是使用者拍板的安全邊界：搬移只影響顯示與試連，不可以讓系統把它當可納管
    （反過來也不行——搬走不等於從白名單踢掉）。
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        conn = _conn(tmp)
        for ip, host in WIN_SSH:
            _seed(conn, ip, host, "Rocky Linux 9")
            _scanned(conn, ip)
        ips = [ip for ip, _ in WIN_SSH]
        _run(conn, ips)

        before = batch_onboard_service.classify_targets(conn, ips)
        for ip, host in WIN_SSH:
            _move(conn, ip, host, cd.TAB_WINRM, "Windows+OpenSSH")
        assert batch_onboard_service.classify_targets(conn, ips) == before

        # 反方向：整批搬去儲存設備（不納入收集的那一側）也一樣不動白名單
        for ip, host in WIN_SSH:
            _move(conn, ip, host, cd.TAB_STORAGE, "改判成儲存")
        assert batch_onboard_service.classify_targets(conn, ips) == before
        conn.close()


def test_前端_每個頁簽都切得到搬移模式_而且勾選用途是明寫的():
    """使用者「我沒辦法一次改」：搬移不再綁在「你在哪個頁簽」，而是一個明寫的模式。"""
    src = ADOPT_VUE.read_text(encoding="utf-8")
    assert "pickMode" in src and "effPickMode" in src, "adopt.vue 沒有明寫的勾選用途切換"
    assert "勾選後要做什麼" in src, "畫面上沒有講清楚勾選是要做什麼"
    # 模式不可以再由頁簽清單決定——那正是「SSH 頁沒有批次搬移」的原因
    assert "MOVE_PICK_TABS" not in src, "勾選用途還綁在頁簽清單上，能動的三頁仍搬不了"
    # 可搬的列不再只在特定頁簽計算
    assert "const movableRows = computed(() => dVisible.value.filter(canMove))" in src


def test_前端_防誤納管的三道鎖都在():
    """紅線：勾了一批想搬移，按到「一次納管」把不該納管的送進去——絕對不能發生。"""
    src = ADOPT_VUE.read_text(encoding="utf-8")

    # 鎖 1：兩組勾選各自獨立，而且「一次納管」送出的永遠只有 probeSelected
    assert "const probeSelected = ref<Set<string>>(new Set())" in src
    assert "const moveSelected = ref<Set<string>>(new Set())" in src
    assert "batchOnboardIps = [...probeSelected]" in src
    assert "batchOnboardIps = [...moveSelected]" not in src, "一次納管竟然吃得到搬移的勾選"
    # probeSelected 只可能由通過 canProbe 的列填進去
    assert "canProbe(r)" in src

    # 鎖 2：搬移模式下「批次試密碼」「一次納管」整個不 render（v-if，不是 disabled）
    tpl = src[src.index("<template>"):]
    tpl = re.sub(r"<!--.*?-->", "", tpl, flags=re.S)
    probebar = tpl[tpl.index("🔑 批次試密碼"):]
    probebar = probebar[:probebar.index("</div>")]
    guard = tpl[:tpl.index("🔑 批次試密碼")]
    # v1.468.6 多了「🏷 加標籤」模式：改成只在「佈收集帳號」模式才 render（搬移、加標籤都不會出現）
    assert 'v-if="onboardMode && probeableRows.length"' in guard, \
        "佈帳號那一排沒有用 onboardMode 擋住——搬移／加標籤模式下還按得到「一次納管」"
    assert "batchOnboardIps = [...tagSelected]" not in src, "一次納管竟然吃得到加標籤的勾選"
    assert "⚡ 勾選的一次納管" in probebar, "兩顆危險按鈕沒有關在同一個 v-if 底下"

    # 鎖 3：換模式／換頁簽一律清空兩個 Set
    assert "function clearPicks()" in src
    assert "moveSelected.value = new Set()" in src and "probeSelected.value = new Set()" in src
    assert "watch(dRoute, () => { clearPicks(); pickMode.value = 'onboard' })" in src
    assert "watch(effPickMode, clearPicks)" in src


# ---------- 全站不再出現「不納管」這個字（2026-10-16）----------
# 使用者看了 1.429.0：「這個沒改到」「每一頁都要」。
# 原意：「網路設備／ESXi／儲存 這些都有它獨立納管方式」「不是靠 Linux 方式納管」。

def test_用語_狀態標籤不再寫不納管():
    assert cd.STATUS_LABEL[cd.STATUS_NOT_ONBOARDABLE] == cd.NOT_ONBOARDABLE_TEXT
    assert "不納管" not in cd.NOT_ONBOARDABLE_TEXT
    assert "不納管" not in cd.NOT_ONBOARDABLE_SENTENCE
    for label in cd.STATUS_LABEL.values():
        assert "不納管" not in label, label
    for label in cd.TAB_LABEL.values():
        assert "不納管" not in label, label


def test_用語_內部識別字一個都沒動():
    """只換給人看的文字。識別字動了會連環影響 API／DB／舊資料。"""
    assert cd.STATUS_NOT_ONBOARDABLE == "not_onboardable"
    assert cd.TAB_NOK_OTHER == "nok_other"
    assert cd.TAB_STORAGE == "storage" and cd.TAB_NETWORK == "network" and cd.TAB_ESXI == "esxi"


def test_用語_逐列訊息改成講該走哪條():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        nas = ("10.99.19.55", "NAS019-055", "儲存設備")
        _seed(conn, *nas)
        r = _run(conn, [nas[0]])["results"][0]
        assert "不納管" not in r["message"], r["message"]
        assert cd.NOT_ONBOARDABLE_TEXT in r["message"]
        # 指路照舊：這一列要講得出該走 SAN 收集
        assert r["guide"]["how"] == "SAN 收集"
        conn.close()


def test_用語_失敗分類跟著改_而且舊訊息仍認得出來():
    """資料庫裡存著舊訊息（寫「不納管」），改字之後不可以變成歸不了類。"""
    import onboard_failures as of
    assert "不納管" not in of.REASON_NOT_ONBOARDABLE
    assert of.REASON_NOT_ONBOARDABLE == cd.NOT_ONBOARDABLE_TEXT
    assert "不納管" not in of.NEXT_STEP[of.REASON_NOT_ONBOARDABLE]
    # 新訊息
    assert of.classify_reason("connect", f"儲存設備——{cd.NOT_ONBOARDABLE_SENTENCE}。依據：資產登記") \
        == of.REASON_NOT_ONBOARDABLE
    # 舊訊息（1.430.0 以前存下來的）
    assert of.classify_reason("connect", "儲存設備——不納管。依據：資產登記") \
        == of.REASON_NOT_ONBOARDABLE


def test_用語_前端各頁都不再顯示不納管():
    """使用者：「每一頁都要」。只看模板（註解裡講歷史不算顯示）。"""
    pages = (ROOT / "APP" / "asset-module" / "frontend" / "pages")
    bad = []
    for f in sorted(pages.glob("*.vue")) + sorted(
            (ROOT / "APP" / "asset-module" / "frontend" / "components").glob("*.vue")):
        src = f.read_text(encoding="utf-8")
        if "<template>" not in src:
            continue
        tpl = src[src.index("<template>"):]
        tpl = tpl[:tpl.index("</template>") + len("</template>")]
        tpl = re.sub(r"<!--.*?-->", "", tpl, flags=re.S)
        if "不納管" in tpl:
            bad.append(f.name)
    assert bad == [], f"這幾頁還顯示著「不納管」：{'、'.join(bad)}"
