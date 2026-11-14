"""WhatsUp 告警規則重播測試（功能地圖 #1 #2 #61 #62 #215 #224；同步定案 ESJE 第 6 點）。

把 fixtures/wug_alert_replay/sequences.json 裡去識別化的快照序列（假設備、10.99.x）依序放進中繼資料夾，
每一步跑一次 wug_alert.tick（中繼匯入 → 事件判定 → 通知），對每一步的預期。不連網路、Teams 不啟用（只看通知紀錄）。

規則 → 釘在哪裡（已有的測試不重寫，只列出來）：
| 規則 | 本檔重播情境 | 既有單元測試 |
|---|---|---|
| 非開盤斷線滿 N 分才推 | 非開盤_真斷線滿10分才推一則… | test_wug_alert::test_斷線未滿確認時間不推_滿了才推一則 |
| 開盤也要斷滿門檻（2.066.x 使用者 11-13：只看需要注意） | 開盤時段_也要斷滿門檻才推… | test_alert_attention::test_通知_進入需要注意推一次_恢復推一次_其他不推 |
| 短暫恢復進 08:00 彙整 | 非開盤_排程重開機7分回來… | test_wug_alert::test_確認時間內就恢復_不立即推_併進08點彙整 |
| 非正式機磁碟緊急進彙整 | （不走 wug_alert，走 disk_alert） | test_teams_disk_alert::test_非正式機緊急進彙整_正式與未登記照推、test_非正式機緊急_彙整關著仍會推但不標緊急 |
| 只推指定動作政策（非主機組不推、未知照推） | 只推主機組政策… | test_wug_alert::test_動作政策篩選_只推指定政策_沒掛的記錄不推_不知道的照推 |
| 同批一致 | 同一批多台…＋本檔 test_同一批_設備順序不同_結果一樣 | test_wug_alert::test_同時多筆紅色合成一則但每台各自一行 |
| 重送不重複 | 中繼重送同一份快照…＋本檔 test_同名批次再放一次_不重匯 | （本檔新補） |
| Maintenance 不推 | WhatsUp維護中… | test_wug_alert::test_Maintenance_照記但不推 |
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "APP" / "asset-module" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import credential_store  # noqa: E402
import db  # noqa: E402
import notice_digest as nd  # noqa: E402
import quiet_window as qw  # noqa: E402
import relay_import as ri  # noqa: E402
import wug_alert as wa  # noqa: E402
from test_wug_alert import ev, notices, snap_batch  # noqa: E402

SEQ = json.loads((Path(__file__).resolve().parent / "fixtures" / "wug_alert_replay" / "sequences.json").read_text(encoding="utf-8"))


@pytest.fixture
def renv(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(credential_store, "KEY_PATH_DEFAULT", str(tmp_path / "k.key"))
    monkeypatch.delenv("WEBIT_TEAMS_ALLOW_LOOPBACK", raising=False)
    monkeypatch.setattr(ri, "STABLE_SECONDS", -5)
    import alert_attention
    monkeypatch.setattr(alert_attention, "DEFAULT_MIN", 0)        # 2.066.x：需要注意門檻＝情境的 confirm_min（_setup 設），沒設＝一斷就算
    monkeypatch.setattr(alert_attention, "DEFAULT_OPEN_MIN", 0)
    db.init_db()
    c = db.get_connection()
    drop = tmp_path / "drop"
    drop.mkdir()
    clock = {"now": datetime(2026, 11, 2, 0, 0)}
    qw.set_clock(lambda: clock["now"])
    db.set_setting(c, ri.DIR_KEY, str(drop))
    db.set_setting(c, ri.ENABLED_KEY, "1")
    c.execute("CREATE TABLE IF NOT EXISTS business_system (api_id TEXT PRIMARY KEY, name TEXT, ap_department TEXT, ap_owner TEXT, "
              "sys_class TEXT, sys_class_override TEXT)")
    for s in SEQ["systems"]:
        c.execute("INSERT OR REPLACE INTO business_system (api_id, name, ap_department, ap_owner, sys_class, sys_class_override) "
                  "VALUES (?,?,?,?,?,NULL)", (s["api_id"], s["name"], "示範部", "示範負責人", s["sys_class"]))
    for a in SEQ["assets"]:
        c.execute("INSERT INTO hardware (asset_serial, hostname, ip, api_id, environment) VALUES (?,?,?,?,?)",
                  (a["serial"], a["hostname"], a["ip"], a["api_id"], a["environment"]))
    c.commit()
    wa.ensure(c)
    yield c, drop, clock
    qw.set_clock(None)
    c.close()


def _dev(d: dict) -> dict:
    mons = []
    for m in d.get("monitors", []):
        x = {"name": m["name"], "type": "active", "status": m["status"], "enabled": "True"}
        if "policy" in m:
            x["policy"] = m["policy"]
        mons.append(x)
    return {"id": d["id"], "name": d.get("name") or f"demo-wug-{d['id']}", "host_name": f"wug{d['id']}.example.test", "ip": d["ip"],
            "worst_state": d.get("state", "Down"), "monitors_ok": d.get("ok", True), "monitors": mons}


def _setup(c, sc: dict) -> None:
    wa.save_settings(c, {**wa.DEFAULTS, "enabled": True, **sc.get("settings", {})})
    nd.set_time(c, sc.get("digest_time", ""))
    if int(sc.get("settings", {}).get("confirm_min") or 0) > 0:          # 2.066.x：需要注意門檻取代確認時間
        import alert_attention
        alert_attention.save_threshold(c, int(sc["settings"]["confirm_min"]), int(sc["settings"]["confirm_min"]))
    c.commit()


def _step(c, drop, clock, at: datetime, devices: list) -> None:
    snap_batch(drop, at, [_dev(d) for d in devices])
    clock["now"] = at
    r = wa.tick(c, at)
    assert r["import"]["status"] == "ok" and r["import"]["rejected"] == 0, r


def _pushed_text(c) -> str:
    return " ".join(n["title"] + " " + (n["summary"] or "") for n in notices(c))


def _check(c, exp: dict, where: str) -> None:
    for kind, n in exp.get("notices", {}).items():
        assert len(notices(c, kind)) == n, f"{where}：{kind} 通知應 {n} 則，實際 {len(notices(c, kind))}"
    if "notices_total" in exp:
        assert len(notices(c)) == exp["notices_total"], f"{where}：通知總數"
    if "events" in exp:
        assert len(ev(c)) == exp["events"], f"{where}：事件筆數"
    for did, tier in exp.get("tiers", {}).items():
        assert {r["tier"] for r in ev(c, wug_device_id=did)} == {tier}, f"{where}：設備 {did} 分級"
    if "nopolicy" in exp:
        got = sorted({r["wug_device_id"] for r in ev(c) if ",nopolicy," in (r["notified"] or "")})
        assert got == sorted(exp["nopolicy"]), f"{where}：不推的設備"
    if "open_events_in_notice" in exp:
        assert [n["events"] for n in notices(c, "open")] == [exp["open_events_in_notice"]], f"{where}：同一則涵蓋的事件數"
    text = _pushed_text(c)
    for s in exp.get("pushed_includes", []):
        assert s in text, f"{where}：通知應含「{s}」"
    for s in exp.get("pushed_excludes", []):
        assert s not in text, f"{where}：通知不應含「{s}」"
    if "digest_pending" in exp:
        assert len(nd.pending(c)) == exp["digest_pending"], f"{where}：彙整待送筆數"
    lines = " ".join(i["line"] for i in nd.pending(c))
    for s in exp.get("digest_contains", []):
        assert s in lines, f"{where}：彙整應含「{s}」"


@pytest.mark.parametrize("name", list(SEQ["scenarios"]))
def test_重播去識別化快照序列(renv, name):
    c, drop, clock = renv
    sc = SEQ["scenarios"][name]
    _setup(c, sc)
    t0 = datetime.strptime(sc["start"], "%Y-%m-%d %H:%M:%S")
    for i, st in enumerate(sc["steps"]):
        _step(c, drop, clock, t0 + timedelta(minutes=st["at_min"]), st["devices"])
        _check(c, st.get("expect", {}), f"{name} 第 {i + 1} 步（+{st['at_min']} 分）")


def test_序列只用10點99假位址():
    """去識別化守門：序列裡的 IP 一律 10.99.x，主機名一律 demo- 開頭。"""
    raw = json.dumps(SEQ, ensure_ascii=False)
    import re
    ips = set(re.findall(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", raw))
    assert ips and all(ip.startswith("10.99.") for ip in ips), ips
    assert all(a["hostname"].startswith("demo-") for a in SEQ["assets"])


def _outcome(c) -> dict:
    """可比較的結果：每個事件的（設備, 監控, 分級, 已排階段）＋每則通知的（種類, 筆數, 提到哪些主機）。"""
    evs = sorted((r["wug_device_id"], r["monitor"], r["tier"], r["notified"]) for r in ev(c))
    hosts = [a["hostname"] for a in SEQ["assets"]]
    ns = [(n["kind"], n["events"], tuple(h for h in hosts if h in n["summary"])) for n in notices(c)]
    return {"events": evs, "notices": ns}


def test_同一批_設備順序不同_結果一樣(renv, tmp_path):
    """同批一致：同一份快照裡設備的先後順序不影響分級、要不要推、推進哪一則（#224）。"""
    c, drop, clock = renv
    sc = SEQ["scenarios"]["同一批多台_合成一則_每台各一行"]
    _setup(c, sc)
    t0 = datetime.strptime(sc["start"], "%Y-%m-%d %H:%M:%S")
    devs = sc["steps"][0]["devices"]
    _step(c, drop, clock, t0, devs)
    _step(c, drop, clock, t0 + timedelta(minutes=5), [])
    a = _outcome(c)
    # 同一份資料庫清空事件與通知，順序倒過來再重播一次
    for t in ("alert_event", "alert_notice", "alert_scan"):
        c.execute(f"DELETE FROM {t}")
    for k in (wa.K_LAST_SNAP, wa.K_LAST_DIGEST):
        c.execute("DELETE FROM app_settings WHERE key = ?", (k,))
    c.commit()
    _step(c, drop, clock, t0 + timedelta(minutes=10), list(reversed(devs)))
    _step(c, drop, clock, t0 + timedelta(minutes=15), [])
    b = _outcome(c)
    assert a["events"] == b["events"]
    assert [(k, n, set(h)) for k, n, h in a["notices"]] == [(k, n, set(h)) for k, n, h in b["notices"]]
    assert [k for k, _n, _h in a["notices"]] == ["open", "recover"]


def test_同名批次再放一次_不重匯_不重推(renv):
    """重送不重複：中繼把同一個批次資料夾再放一次（同名），只匯入一次、不多開事件、不多推（#224）。"""
    c, drop, clock = renv
    _setup(c, {"settings": {"confirm_min": 10}})
    t0 = datetime(2026, 11, 2, 10, 0)
    devs = [_dev({"id": "41", "ip": "10.99.7.11", "monitors": [{"name": "Ping", "status": "Down at least 12 min"}]})]
    folder = snap_batch(drop, t0, devs, name="wugalert_20261102T100000_resend")
    clock["now"] = t0
    wa.tick(c, t0)
    assert len(ev(c)) == 1 and len(notices(c, "open")) == 1
    # 中繼重送：同名資料夾內容重寫一次（時間戳更新），再跑兩輪
    import shutil
    shutil.rmtree(folder)
    snap_batch(drop, t0, devs, name="wugalert_20261102T100000_resend")
    for m in (1, 2):
        clock["now"] = t0 + timedelta(minutes=m)
        r = wa.tick(c, clock["now"])
        assert r["import"]["status"] == "idle", r
    n_imp = c.execute("SELECT COUNT(*) FROM relay_import_batches WHERE source = 'whatsup_alert' AND status = 'imported'").fetchone()[0]
    assert n_imp == 1
    assert len(ev(c)) == 1 and len(notices(c, "open")) == 1 and len(notices(c)) == 1
