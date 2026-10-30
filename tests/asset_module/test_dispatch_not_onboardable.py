"""收集分派：ESXi／設備有開 SSH 也要標「不納管」，而且連試連都不做（2026-09-14）。

使用者：「很明顯看得出是 VMware，可能是 ESXi……雖然它有 SSH，但應該要有一個標記，
這樣我就會不去納管它」「我的定義雖然是 Linux，但這種設備我不能納管」。
另：收集帳號連得上的，畫面要寫「已納管」。
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))

import collect_dispatch as cd  # noqa: E402
import db  # noqa: E402
import onboard_eligibility as oe  # noqa: E402


def _conn(tmp):
    p = Path(tmp) / "t.db"
    db.init_db(p)
    return db.get_connection(p)


def _seed(conn, ip, serial, os=None, hostname=None):
    db.insert_hardware(conn, asset_serial=serial, ip=ip, os=os, hostname=hostname,
                       environment="測試", asset_status="使用中")


def _ssh_open(ip):
    return [22, 443]


class _SpySSH:
    def __init__(self):
        self.called = []

    def __call__(self, ip):
        self.called.append(ip)
        return True, None


def test_作業系統登記是ESXi_標不納管_不去試連():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, "192.0.2.11", "A-1", os="VMware ESXi 8.0.3", hostname="esx-example-01")
        spy = _SpySSH()
        out = cd.run_dispatch(conn, "192.0.2.11", prober=_ssh_open, ssh_prober=spy)
        r = out["results"][0]
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE
        assert "VMware ESXi" in r["message"] and "作業系統" in r["message"]
        assert spy.called == [], "不納管的機器連試連都不該做"
        assert out["needs_action"] == 0, "不納管的不算要人工處理"
        assert out["not_onboardable"] == 1
        conn.close()


def test_作業系統沒填_但vCenter列為ESXi主機_也要擋():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, "192.0.2.12", "A-2", os=None, hostname="ESX-EXAMPLE-02")
        conn.execute("INSERT INTO source_record (source, source_key, payload) VALUES (?,?,?)",
                     ("vcenter", "vm-1", json.dumps({"esxi_host": "esx-example-02.example.local"})))
        conn.commit()
        spy = _SpySSH()
        out = cd.run_dispatch(conn, "192.0.2.12", prober=_ssh_open, ssh_prober=spy)
        r = out["results"][0]
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE
        assert "vCenter" in r["message"]
        assert spy.called == []
        conn.close()


def test_vCenter用IP列ESXi主機_IP不能被去網域切壞():
    names = {"192.0.2.13"}
    assert oe._name_keys("192.0.2.13") == {"192.0.2.13"}
    assert oe.not_onboardable(None, None, "192.0.2.13", names)[0] == "esxi"
    assert oe.not_onboardable(None, None, "192.0.2.99", names) is None


def test_一般Linux照常試連_連得上寫已納管():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, "192.0.2.14", "A-4", os="Rocky Linux 9", hostname="app-example-01")
        spy = _SpySSH()
        out = cd.run_dispatch(conn, "192.0.2.14", prober=_ssh_open, ssh_prober=spy)
        r = out["results"][0]
        assert r["status"] == cd.STATUS_COLLECTED
        assert cd.STATUS_LABEL[cd.STATUS_COLLECTED] == "已納管"
        assert spy.called == ["192.0.2.14"]
        conn.close()


def test_OpenShift節點與設備也標不納管():
    assert oe.not_onboardable("Red Hat Enterprise Linux CoreOS 4.14")[0] == "immutable"
    assert oe.not_onboardable("Idrac 7.00.00.172")[0] == "appliance"
    assert oe.not_onboardable("Rocky Linux 9") is None


# ---- SSH banner 自報身分（2026-09-15：10.92.198.21 是 VMware Avi Load Balancer）----

def test_banner自報VMware_Avi就不納管():
    import onboard_eligibility as oe2
    msg = ('22 通、但收集帳號 webit3scan 進不去（Avi Cloud Controller；VMware Avi Load Balancer '
           'software, Copyright (C) 2013-2026 by Broadcom, Inc.）')
    assert oe2.product_from_banner(msg) == 'VMware Avi Load Balancer（NSX ALB）'


def test_一般Linux的banner不會被誤標():
    import onboard_eligibility as oe2
    assert oe2.product_from_banner('Permission denied (publickey).') is None
    assert oe2.product_from_banner('SSH-2.0-OpenSSH_8.7') is None
    assert oe2.product_from_banner('') is None


def test_收集帳號進不去且banner自報設備_標不納管():
    import tempfile as _tf
    with _tf.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, '192.0.2.21', 'A-21', os=None, hostname='lb-example-01')
        def _denied(ip):
            return False, 'Avi Cloud Controller; VMware Avi Load Balancer software. Permission denied (publickey).'
        out = cd.run_dispatch(conn, '192.0.2.21', prober=_ssh_open, ssh_prober=_denied)
        r = out["results"][0]
        assert r['status'] == cd.STATUS_NOT_ONBOARDABLE
        assert 'Avi' in r['message'] and 'banner' in r['message']
        assert out['needs_action'] == 0
        conn.close()


# ---- 不納管再依類別拆開（2026-10-13：「不納管（ESXi／設備）11」看不出幾台是儲存）----
#
# 使用者原話：「是 storage 你怎麼抓不出來，可把 storage 獨立分類出來」。
# 列的層級已經寫「儲存設備——不納管」，但上面的統計方塊還是合計的。

def _seed_purpose(conn, ip, serial, purpose, os=None, name=None):
    db.insert_hardware(conn, asset_serial=serial, ip=ip, os=os, hostname=serial,
                       asset_name=name, asset_purpose=purpose,
                       environment="測試", asset_status="使用中")


def test_有儲存設備時_不納管的類別計數要對():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        # 儲存兩台：一台 os 欄就寫著、一台 os 欄空白但用途寫著（兩條路都要算進同一類）
        _seed(conn, "192.0.2.31", "S-1", os="NetApp ONTAP 9.12", hostname="nas-example-01")
        _seed_purpose(conn, "192.0.2.32", "S-2", "EMC UnityXT480 NAS (iSCSI target)", os="N/A")
        _seed(conn, "192.0.2.33", "E-1", os="VMware ESXi 8.0.3", hostname="esx-example-11")
        _seed(conn, "192.0.2.34", "I-1", os="Red Hat Enterprise Linux CoreOS 4.14",
              hostname="ocp-example-01")
        _seed(conn, "192.0.2.35", "A-1", os="Idrac 7.00.00.172", hostname="idrac-example-01")
        _seed(conn, "192.0.2.36", "L-1", os="Rocky Linux 9", hostname="app-example-11")
        targets = "\n".join(f"192.0.2.{n}" for n in range(31, 37))
        spy = _SpySSH()
        out = cd.run_dispatch(conn, targets, prober=_ssh_open, ssh_prober=spy)

        assert out["not_onboardable"] == 5
        assert out["not_onboardable_by_kind"] == {
            "storage": 2, "esxi": 1, "immutable": 1, "appliance": 1}
        # 小計要加得回方塊上的總數，不然使用者得自己猜剩下幾台去哪了
        assert sum(out["not_onboardable_by_kind"].values()) == out["not_onboardable"]
        # 只有 Linux 那台被試連
        assert spy.called == ["192.0.2.36"]

        # 重新整理（從 DB 還原）也要拆得出來——類別有存進逐台結果，不是只活在記憶體裡
        again = cd.latest_run(conn)
        assert again["not_onboardable_by_kind"] == out["not_onboardable_by_kind"]
        conn.close()


def test_沒有儲存設備時_那一類不出現():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, "192.0.2.41", "E-2", os="VMware ESXi 8.0.3", hostname="esx-example-21")
        _seed(conn, "192.0.2.42", "L-2", os="Rocky Linux 9", hostname="app-example-21")
        out = cd.run_dispatch(conn, "192.0.2.41\n192.0.2.42",
                              prober=_ssh_open, ssh_prober=_SpySSH())
        # 0 的類別一律不出現（不補 0）：畫面列一排 0 等於要人自己過濾
        assert out["not_onboardable_by_kind"] == {"esxi": 1}
        assert "storage" not in out["not_onboardable_by_kind"]
        conn.close()


def test_全部可納管時_類別統計是空的():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, "192.0.2.51", "L-3", os="Rocky Linux 9", hostname="app-example-31")
        out = cd.run_dispatch(conn, "192.0.2.51", prober=_ssh_open, ssh_prober=_SpySSH())
        assert out["not_onboardable"] == 0
        assert out["not_onboardable_by_kind"] == {}
        conn.close()


def test_banner擋下來的也有類別_小計才加得回總數():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, "192.0.2.61", "B-1", os=None, hostname="lb-example-11")

        def _denied(ip):
            return False, ("Avi Cloud Controller; VMware Avi Load Balancer software. "
                           "Permission denied (publickey).")

        out = cd.run_dispatch(conn, "192.0.2.61", prober=_ssh_open, ssh_prober=_denied)
        assert out["not_onboardable"] == 1
        assert out["not_onboardable_by_kind"] == {oe.KIND_BANNER: 1}
        assert sum(out["not_onboardable_by_kind"].values()) == out["not_onboardable"]
        conn.close()


def test_類別標籤用公開名稱_不要存取底線開頭的名字():
    assert oe.NOT_ONBOARDABLE_LABEL["storage"] == "儲存設備"
    assert not hasattr(oe, "_NOT_ONBOARDABLE_LABEL")
    assert oe.kind_label("storage") == "儲存設備"
    assert oe.kind_label(oe.KIND_BANNER)
    # 認不出來的類別回原字串，不吞掉——吞掉會讓畫面少一格、對不上總數
    assert oe.kind_label("something-new") == "something-new"
