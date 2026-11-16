"""主機規格（OS／硬體）排程每週更新（2026-11-13）：掛在掃描後處理、只收超過 N 天沒收的、可關、盤中不連線、
VMware 永遠不收；覆蓋率的規格門檻改用 spec_stale_days（預設 14 天）。"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "APP" / "asset-module" / "backend"))

import db  # noqa: E402
import data_coverage as dc  # noqa: E402
import host_spec_collector as hsc  # noqa: E402
import manage_state  # noqa: E402
import quiet_window as qw  # noqa: E402
import scan_service  # noqa: E402

RHEL = "Red Hat Enterprise Linux 9"


def _t(days: float) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _hw(c, sn, ip, os_=RHEL, ok=1):
    db.insert_hardware(c, asset_serial=sn, hostname=sn.lower(), ip=ip, os=os_, asset_status="使用中",
                       collect_ok=ok, collect_checked_at=_t(0))


def _spec(c, sn, ip, days):
    c.execute("INSERT INTO host_spec (asset_serial, ip, platform, cpu, collected_at) VALUES (?,?,?,?,?)",
              (sn, ip, "linux", "x", _t(days)))


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "t.db"
    db.init_db(p)
    c = db.get_connection(p)
    _hw(c, "FRESH", "10.99.5.1")
    _spec(c, "FRESH", "10.99.5.1", 2)          # 2 天前收過 → 還沒到期
    _hw(c, "OLD", "10.99.5.2")
    _spec(c, "OLD", "10.99.5.2", 10)           # 10 天前 → 到期
    _hw(c, "NEVER", "10.99.5.3")               # 從沒收過 → 到期
    _hw(c, "UNM", "10.99.5.4", ok=None)        # 未納管 → 不收
    _hw(c, "ESX", "10.99.5.5", os_="VMware ESXi 8.0.3")   # 天前就納管的 ESXi（collect_ok=1）→ 天條：永遠不收
    _hw(c, "VCSA", "10.99.5.6", os_="VMware vCenter Server Appliance 8.0")
    c.commit()
    yield c
    c.close()


def test_掃描後處理鏈含規格步驟_排在軟體之後():
    steps = [s for s, _ in scan_service._POST_SCAN_STEPS]
    assert "spec" in steps and steps.index("software") < steps.index("spec")
    assert "scan_post_spec" in scan_service.CATCHUP
    assert qw.ENTRIES["scan_post_spec"]["cls"] == "A"


def test_只挑超過N天或從沒收過的_未納管不挑(conn):
    assert hsc.due_serials(conn, 7) >= {"OLD", "NEVER"}
    assert not hsc.due_serials(conn, 7) & {"FRESH", "UNM"}
    assert "OLD" not in hsc.due_serials(conn, 14)            # 間隔設長一點，10 天前的就還沒到期
    db.set_setting(conn, "spec_collect_interval_days", "1")
    assert hsc.spec_interval_days(conn) == 1 and "FRESH" in hsc.due_serials(conn, hsc.spec_interval_days(conn))
    db.set_setting(conn, "spec_collect_interval_days", "abc")
    assert hsc.spec_interval_days(conn) == 7


def test_排程只收到期的_VMware永遠不連(conn, monkeypatch):
    touched = []
    monkeypatch.setattr(manage_state, "_runner_for", lambda ip, *a, **k: touched.append(ip) or (lambda h, cmd: ""))
    monkeypatch.setattr(hsc, "collect", lambda runner, ip, platform, is_vm=False: {"spec": {}, "platform": platform})
    r = hsc.run_scheduled_refresh(conn)
    assert set(touched) == {"10.99.5.2", "10.99.5.3"}, touched      # 只有 OLD、NEVER；FRESH／未納管／ESXi／vCenter 都沒連
    assert r["unsupported"] == 2                                     # ESXi、vCenter 照舊列為不支援


def test_沒有到期的就完全不呼叫收集(conn, monkeypatch):
    conn.execute("UPDATE hardware SET collect_ok = NULL WHERE asset_serial IN ('OLD','NEVER','ESX','VCSA')")
    conn.commit()
    monkeypatch.setattr(hsc, "run_collection", lambda *a, **k: pytest.fail("不該收"))
    assert hsc.run_scheduled_refresh(conn)["due"] == 0


def test_設定關閉就跳過_失敗不外拋(conn, monkeypatch):
    hit = []
    monkeypatch.setattr(hsc, "run_scheduled_refresh", lambda c: hit.append(1))
    db.set_setting(conn, "spec_collect_enabled", "0")
    scan_service._post_scan_collect_spec(conn)
    assert hit == []
    db.set_setting(conn, "spec_collect_enabled", "1")
    scan_service._post_scan_collect_spec(conn)
    assert hit == [1]

    def boom(c):
        raise RuntimeError("ssh 全掛")
    monkeypatch.setattr(hsc, "run_scheduled_refresh", boom)
    scan_service._post_scan_collect_spec(conn)                       # 不該拋


def test_盤中不連線_記延後(conn, monkeypatch):
    hit = []
    monkeypatch.setattr(hsc, "run_scheduled_refresh", lambda c: hit.append(1))
    monkeypatch.setattr(qw, "in_quiet_window", lambda *a, **k: True)
    with qw.use(qw.Run("scan_schedule", human=False)):
        scan_service._post_scan_collect_spec(conn)
    assert hit == []
    assert "scan_post_spec" in {p["entry"] for p in qw.pending_list(conn)}


def test_覆蓋率規格門檻是14天_不跟收集摘要的8天(conn):
    assert dc.stale_days_for(conn, "spec") == 14 and dc.stale_days_for(conn, "storage") == 14
    _spec(conn, "NEVER", "10.99.5.3", 10)                            # 10 天前：超過 8 天但在 14 天內
    conn.commit()
    r = dc.coverage(conn, "spec")
    by = {x["serial"]: x["state"] for x in r["rows"]}
    assert by["NEVER"] == dc.OK and by["OLD"] == dc.OK and by["FRESH"] == dc.OK
    assert len(dc.drill(conn, "spec", dc.OK)["rows"]) == r["counts"][dc.OK]      # 加總＝下鑽
    db.set_setting(conn, "spec_stale_days", "5")
    conn.commit()
    assert {x["serial"]: x["state"] for x in dc.coverage(conn, "spec")["rows"]}["OLD"] == dc.STALE


def _fake_collect(monkeypatch, touched, fail=True):
    monkeypatch.setattr(manage_state, "_runner_for", lambda ip, *a, **k: touched.append(ip) or (lambda h, cmd: ""))
    spec = {} if fail else {"cpu": "x"}
    monkeypatch.setattr(hsc, "collect", lambda runner, ip, platform, is_vm=False: {"spec": spec, "platform": platform})
    if not fail:
        monkeypatch.setattr(hsc, "_any_spec", lambda res: True)
        monkeypatch.setattr(hsc, "store", lambda c, sn, ip, res: c.execute(
            "INSERT INTO host_spec (asset_serial, ip, platform, cpu, collected_at) "
            "VALUES (?,?,?,?,datetime('now','localtime')) "
            "ON CONFLICT(asset_serial) DO UPDATE SET collected_at = excluded.collected_at", (sn, ip, "linux", "x")))
        import net_collector
        monkeypatch.setattr(net_collector, "collect_net", lambda *a, **k: {})
        monkeypatch.setattr(net_collector, "store_net", lambda *a, **k: None)


def test_排程逐台重查靜默窗_第二台前進窗就停_其餘延後(conn, monkeypatch):
    touched = []
    _fake_collect(monkeypatch, touched)
    monkeypatch.setattr(qw, "in_quiet_window", lambda *a, **k: len(touched) >= 1)   # 連完第一台後進窗
    with qw.use(qw.Run("scan_schedule", human=False)):
        hsc.run_scheduled_refresh(conn)
    assert len(touched) == 1, touched
    assert "scan_post_spec" in {p["entry"] for p in qw.pending_list(conn)}
    # 沒連到的那台沒記冷卻：窗外補跑時仍到期
    monkeypatch.setattr(qw, "in_quiet_window", lambda *a, **k: False)
    assert len(hsc.due_serials(conn, 7) & {"OLD", "NEVER"}) == 1


def test_人工收集不逐台查靜默窗_也不記冷卻(conn, monkeypatch):
    touched = []
    _fake_collect(monkeypatch, touched)
    monkeypatch.setattr(qw, "checkpoint", lambda *a, **k: pytest.fail("人工路徑不該逐台查（行為不變）"))
    hsc.run_collection(conn, trigger="manual")
    assert {"10.99.5.2", "10.99.5.3"} <= set(touched)
    assert hsc._load_attempts(conn) == {}


def test_一直失敗的主機_冷卻期內不再每晚連(conn, monkeypatch):
    touched = []
    _fake_collect(monkeypatch, touched, fail=True)
    hsc.run_scheduled_refresh(conn)
    assert set(touched) == {"10.99.5.2", "10.99.5.3"}
    assert hsc.due_serials(conn, 7) & {"OLD", "NEVER"} == set()       # 收失敗 collected_at 沒變，但 7 天內試過 → 不挑
    touched.clear()
    hsc.run_scheduled_refresh(conn)                                    # 隔晚再跑：不連
    assert touched == []
    db.set_setting(conn, hsc.SPEC_ATTEMPT_SETTING, '{"OLD": "%s", "NEVER": "%s"}' % (_t(8), _t(8)))
    assert hsc.due_serials(conn, 7) >= {"OLD", "NEVER"}               # 冷卻過了又到期


def test_收成功的_下週才再收(conn, monkeypatch):
    touched = []
    _fake_collect(monkeypatch, touched, fail=False)
    hsc.run_scheduled_refresh(conn)
    assert set(touched) == {"10.99.5.2", "10.99.5.3"}
    db.set_setting(conn, hsc.SPEC_ATTEMPT_SETTING, "{}")              # 就算沒有嘗試紀錄，collected_at 也已更新
    assert hsc.due_serials(conn, 7) & {"OLD", "NEVER"} == set()
