"""ESXi 沒開 SSH 也要落在 ESXi 頁簽，不是「待裝 Agent」（2026-10-16）。

使用者在公司機 /adopt 看到三台 `Esxi019-56/57/58`（資產庫 os 欄登記
`VMware ESXi, 8.0.3`）被列成：

    開放埠 無 ｜ 走哪條路 Push Agent ｜ 待裝 Agent
    活著（開放埠 無）但 22／445 都不通，系統進不去

而 ESXi 頁簽是 0。原話：「這些應該算在 ESXi 但沒進去」。

根因：不納管判定被關在 `if route == ROUTE_SSH` 裡。ESXi 預設不開 SSH
（只開 443/902），探測結果是「活著、開放埠無」→ choose_route 回 agent
→ 判定根本沒跑。而「請人去 ESXi 上裝 Push Agent」本身就是錯的建議。

這支釘住：
  1. 活著、沒有任何開放埠、os 登記 ESXi → 不納管、ESXi 頁簽、不給安裝包
  2. WinRM 那條路（445／5985）上的設備也一樣擋得到
  3. **完全沒回應的不判**——登記在案卻叫不動是真的待辦，不可以被換成「不納管」
  4. 收得到的（collected）不可以被重新標成不納管
  5. 舊 run（判定沒跑到、存成 needs_agent）讀出來也要歸到 ESXi
  6. 主機名長得像 ESXi 不算證據（白名單設計，失敗方向是漏標不是誤標）
"""
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


def _seed(conn, ip, serial, os=None, hostname=None, name=None, purpose=None):
    db.insert_hardware(conn, asset_serial=serial, ip=ip, os=os, hostname=hostname,
                       asset_name=name, asset_purpose=purpose,
                       environment="測試", asset_status="使用中")


class _SpySSH:
    def __init__(self):
        self.called = []

    def __call__(self, ip):
        self.called.append(ip)
        return True, None


def _alive_no_ports(ip):
    """活著但探測埠（22/445/3389/5985）全不通——ESXi 預設就是這樣（只開 443/902）。"""
    return []


def test_活著沒有任何開放埠_os登記ESXi_要進ESXi頁簽():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, "10.99.19.56", "HW-1", os="VMware ESXi, 8.0.3",
              hostname="esx-example-56", name="虛擬化平台", purpose="VMware ESXi")
        spy = _SpySSH()
        out = cd.run_dispatch(conn, "10.99.19.56", prober=_alive_no_ports, ssh_prober=spy)
        r = out["results"][0]

        assert r["alive"] == 1 and not r["open_ports"]
        assert r["route"] == cd.ROUTE_AGENT, "route 照舊反映探測結果（這台真的只能被動回報）"
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE, \
            f"應該是不納管，不是 {cd.STATUS_LABEL.get(r['status'])}"
        assert r["not_onboardable_kind"] == "esxi"
        assert r["tab"] == cd.TAB_ESXI, "使用者原話：這些應該算在 ESXi"
        assert out["by_tab"][cd.TAB_ESXI] == 1
        assert out["by_tab"][cd.TAB_AGENT] == 0, "不可以再叫人去 ESXi 上裝 Push Agent"
        assert "VMware ESXi" in r["message"] and "作業系統" in r["message"]
        assert out["needs_action"] == 0
        assert spy.called == []

        # 重新整理（從 DB 還原）結論一樣
        again = cd.latest_run(conn)
        assert again["by_tab"][cd.TAB_ESXI] == 1
        assert again["results"][0]["status"] == cd.STATUS_NOT_ONBOARDABLE
        conn.close()


def test_WinRM那條路上的設備也擋得到():
    """445 開著的儲存設備（NAS 的 SMB）不該去試 WinRM 收集。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, "10.99.19.207", "HW-2", os="儲存設備", hostname="nas-example-207",
              purpose="EMC UnityXT480 NAS (iSCSI target)")
        calls = []

        def _winrm(*a, **k):
            calls.append(a)
            return {}

        out = cd.run_dispatch(conn, "10.99.19.207", prober=lambda ip: [445, 5985],
                              winrm_runner=_winrm)
        r = out["results"][0]
        assert r["route"] == cd.ROUTE_WINRM
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE
        assert r["tab"] == cd.TAB_STORAGE
        assert calls == [], "不納管的機器連收集都不該試"
        conn.close()


def test_完全沒回應的不判不納管_登記在案叫不動仍是待辦():
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, "10.99.19.60", "HW-3", os="VMware ESXi, 8.0.3", hostname="esx-example-60")
        out = cd.run_dispatch(conn, "10.99.19.60", prober=lambda ip: None)
        r = out["results"][0]
        assert r["alive"] == 0
        assert r["status"] == cd.STATUS_IMPORT_ONLY, \
            "完全沒回應就是完全沒回應——標成不納管會少講它現在連不上"
        assert r["tab"] == cd.TAB_IMPORT
        assert out["by_tab"][cd.TAB_ESXI] == 0
        # 登記在案卻叫不動＝真的待辦，不可以被抹掉
        assert out["empty_addresses"] == 0
        assert out["needs_action"] == 1
        # 讀取時的重判也不可以動它
        again = cd.latest_run(conn)
        assert again["results"][0]["status"] == cd.STATUS_IMPORT_ONLY
        assert again["needs_action"] == 1
        conn.close()


def test_已經納管成功的不會被重新標成不納管():
    """os 欄登記是設備、但收集帳號真的連得上——收得到就是收得到。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        # Hyper-V 不在可納管白名單（classify_os → appliance），但這台 SSH 真的通
        _seed(conn, "10.99.19.61", "HW-4", os="Rocky Linux 9", hostname="app-example-61")
        out = cd.run_dispatch(conn, "10.99.19.61", prober=lambda ip: [22],
                              ssh_prober=lambda ip: (True, None))
        assert out["results"][0]["status"] == cd.STATUS_COLLECTED

        # 之後有人把 os 欄改成設備：讀取時的重判**不可以**把已納管的改掉
        conn.execute("UPDATE hardware SET os = ? WHERE ip = ?",
                     ("Idrac 7.00.00.172", "10.99.19.61"))
        conn.commit()
        again = cd.latest_run(conn)
        assert again["results"][0]["status"] == cd.STATUS_COLLECTED
        assert again["by_tab"][cd.TAB_SSH] == 1
        conn.close()


def test_舊run存成待裝Agent_讀出來照現在的登記重判到ESXi():
    """升版後不重跑收集，舊結果也要看得出來（不然 ESXi 頁簽還是 0）。"""
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        _seed(conn, "10.99.19.57", "HW-5", os="VMware ESXi, 8.0.3", hostname="esx-example-57")
        cd.run_dispatch(conn, "10.99.19.57", prober=_alive_no_ports)
        # 模擬 1.427.x 之前跑的 run：判定沒跑到，寫成 needs_agent
        conn.execute("UPDATE collect_dispatch_result SET status = ?, "
                     "not_onboardable_kind = NULL, message = ? WHERE ip = ?",
                     (cd.STATUS_NEEDS_AGENT, "活著（開放埠 無）但 22／445 都不通，系統進不去",
                      "10.99.19.57"))
        conn.commit()

        out = cd.latest_run(conn)
        r = out["results"][0]
        assert r["status"] == cd.STATUS_NOT_ONBOARDABLE
        assert r["tab"] == cd.TAB_ESXI
        assert out["by_tab"][cd.TAB_ESXI] == 1 and out["by_tab"][cd.TAB_AGENT] == 0
        conn.close()


def test_主機名長得像ESXi不算證據():
    """白名單設計：合法證據只有 os 欄／資產名稱用途／RVTools／banner。"""
    assert oe.not_onboardable(None, "Esxi019-56", "10.99.19.56") is None
    assert oe.not_onboardable("Rocky Linux 9", "Esxi019-56", "10.99.19.56") is None
    with tempfile.TemporaryDirectory() as tmp:
        conn = _conn(tmp)
        # 沒登記資產、主機名也不在資產庫裡 → 活著沒埠就是待裝 Agent（行為不變）
        out = cd.run_dispatch(conn, "10.99.19.99", prober=_alive_no_ports)
        r = out["results"][0]
        assert r["status"] == cd.STATUS_NEEDS_AGENT
        assert r["tab"] == cd.TAB_AGENT
        conn.close()
