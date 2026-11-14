"""2.064.0：1.5 每日檢查 VMware 段（正本 2-1～2-4）「系統預填、人確認」。全部假資料（vc-a.example.test、10.99.x、假 ESXi 名稱）。

釘住：parse 拒收壞資料、快速通道匯入（一般排程不碰）、零筆阻擋、分頁不完整、收集器停收、批次太舊、應有清單缺／多、
本機碟判斷（類型＋掛載數優先、名稱只輔助）、資料存放區 ID 去重、門檻口徑、警示分列與靜音逾期、已檢視必勾、覆寫留痕、
補抓後重新產生＝新版本＋舊版作廢、設定改動留紀錄、verify_numbers 不變式、天條（不連 ESXi／vCenter、不建帳號、不 SSH）。
"""
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACK = ROOT / "APP" / "asset-module" / "backend"
sys.path.insert(0, str(BACK))

import aria_dailycheck as ad  # noqa: E402
import daily_check as dc  # noqa: E402
import db  # noqa: E402
import quiet_window as qw  # noqa: E402
import relay_import as ri  # noqa: E402
import verify_numbers as vn  # noqa: E402

TPE = timezone(timedelta(hours=8))
VCID = "vcid-aaaa-1111"
VCID2 = "vcid-bbbb-2222"


def tnow():
    return datetime.now(TPE).replace(tzinfo=None, microsecond=0)


def ms(d: datetime) -> int:
    return int(d.replace(tzinfo=TPE).timestamp() * 1000)


def snap(now=None, hosts=None, dss=None, alerts=None, adapters=None, **over):
    now = now or tnow()
    t = ms(now - timedelta(minutes=5))
    if adapters is None:
        adapters = [{"id": "ad-1", "name": "vc-a.example.test", "vcid": VCID, "last_collected": t, "last_heartbeat": t},
                    {"id": "ad-2", "name": "vc-b.example.test", "vcid": VCID2, "last_collected": t, "last_heartbeat": t}]
    if hosts is None:
        hosts = [{"id": "h1", "name": "esxi-a01.example.test", "vcid": VCID, "conn": "connected", "maint": "notInMaintenance", "at": t},
                 {"id": "h2", "name": "esxi-a02", "vcid": VCID, "conn": "disconnected", "maint": "notInMaintenance", "at": t},
                 {"id": "h3", "name": "esxi-a03", "vcid": VCID, "conn": "connected", "maint": "inMaintenance", "at": t},
                 {"id": "h9", "name": "esxi-a09", "vcid": VCID, "conn": "connected", "maint": "notInMaintenance", "at": t},
                 {"id": "hb1", "name": "esxi-b01", "vcid": VCID2, "conn": "connected", "maint": "notInMaintenance", "at": t}]
    if dss is None:
        dss = [{"id": "d1", "name": "SAN-LUN-01", "vcid": VCID, "type": "VMFS", "cap_gb": 4000, "free_gb": 500.0, "hosts": 4, "at": t},
               {"id": "d1", "name": "SAN-LUN-01", "vcid": VCID, "type": "VMFS", "cap_gb": 4000, "free_gb": 500.0, "hosts": 4, "at": t},  # 重複 ID
               {"id": "d2", "name": "SAN-LUN-02", "vcid": VCID, "type": "VMFS", "cap_gb": 4000, "free_gb": 600.0, "hosts": 4, "at": t},
               {"id": "d3", "name": "datastore1", "vcid": VCID, "type": "VMFS", "cap_gb": 200, "free_gb": 10.0, "hosts": 1, "at": t},     # 本機碟
               {"id": "d4", "name": "datastore1 (2)", "vcid": VCID, "type": "VMFS", "cap_gb": 200, "free_gb": 20.0, "hosts": None, "at": t},  # 判不出
               {"id": "d5", "name": "NFS-01", "vcid": VCID, "type": "NFS", "cap_gb": 1000, "free_gb": None, "hosts": 1, "at": t},
               {"id": "db1", "name": "SAN-B-01", "vcid": VCID2, "type": "VMFS", "cap_gb": 4000, "free_gb": 3000.0, "hosts": 2, "at": t}]
    if alerts is None:
        alerts = [{"id": "a1", "res_id": "h2", "res_kind": "HostSystem", "res_name": "esxi-a02", "vcid": VCID, "name": "Host disconnected",
                   "level": "CRITICAL", "status": "ACTIVE", "control": "OPEN", "owner": "", "start": t},
                  {"id": "a2", "res_id": "d1", "res_kind": "Datastore", "res_name": "SAN-LUN-01", "vcid": VCID, "name": "Datastore low",
                   "level": "WARNING", "status": "ACTIVE", "control": "SUPPRESSED", "owner": "", "start": t},
                  {"id": "a3", "res_id": "vm1", "res_kind": "VirtualMachine", "res_name": "vm-x", "vcid": VCID, "name": "VM snapshot old",
                   "level": "IMMEDIATE", "status": "ACTIVE", "control": "ASSIGNED", "owner": "ops", "start": t},
                  {"id": "a4", "res_id": "vm2", "res_kind": "VirtualMachine", "res_name": "vm-y", "vcid": VCID, "name": "Known noisy",
                   "level": "WARNING", "status": "ACTIVE", "control": "OPEN", "owner": "", "start": t},
                  {"id": "a5", "res_id": "vm3", "res_kind": "VirtualMachine", "res_name": "vm-z", "vcid": VCID, "name": "Info only",
                   "level": "INFORMATION", "status": "ACTIVE", "control": "OPEN", "owner": "", "start": t}]
    d = {"aria": {"ok": True, "error": ""},
         "adapters": {"ok": True, "error": "", "total": len(adapters), "rows": adapters},
         "hosts": {"ok": True, "error": "", "total": len(hosts), "rows": hosts},
         "datastores": {"ok": True, "error": "", "total": len(dss), "rows": dss},
         "alerts": {"ok": True, "error": "", "total": len(alerts) + 7, "fetched": len(alerts) + 7, "rows": alerts}}
    for k, v in over.items():
        d[k] = v
    return d


def send(drop, name, data, when=None, counts="auto", fname="snap.json"):
    when = when or datetime.now(TPE)
    d = Path(drop) / name
    d.mkdir(parents=True)
    body = json.dumps(data, ensure_ascii=False).encode()
    (d / fname).write_bytes(body)
    m = {"source": "aria_dailycheck", "batch_id": name, "schema_version": 1, "collector_version": "aria_daily_snap 1",
         "collected_at": when.isoformat(timespec="seconds"), "time_zone": "Asia/Taipei", "range": {"from": "2026-11-13", "to": "2026-11-13"},
         "count": 1, "files": [{"name": fname, "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}],
         "raw": [{"name": "raw_001_hosts.json", "sha256": "ab" * 32}]}
    if counts == "auto":
        m["counts"] = {k: len(((data.get(k) if isinstance(data, dict) else None) or {}).get("rows") or []) for k in ad.KINDS}
    elif counts is not None:
        m["counts"] = counts
    (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    return d


VCS = [{"name": "板橋正式（示範）", "vcid": VCID, "expected": ["esxi-a01", "esxi-a02", "esxi-a03", "esxi-a04"]},
       {"name": "敦南正式（示範）", "vcid": VCID2, "expected": ["esxi-b01"]}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    c = db.get_connection()
    drop = tmp_path / "drop"
    drop.mkdir()
    monkeypatch.setattr(ri, "STABLE_SECONDS", -5)
    db.set_setting(c, ri.DIR_KEY, str(drop))
    db.set_setting(c, ri.ENABLED_KEY, "1")
    c.commit()
    dc.save_settings(c, {"vm_vcenters": VCS}, today=tnow().date(), by="admin")
    yield c, drop
    qw.set_clock(None)
    c.close()


def gen(c, now=None):
    rid = dc.generate(c, "tester", "manual", now or tnow())
    v = dc.run_view(c, rid)
    return {r["code"]: r for r in v["rows"]}, v, rid


def states(row, kind):
    return {d["name"]: d["state"] for d in row["detail"] if d["kind"] == kind}


# ---------------------------------------------------------------- 匯入
@pytest.mark.parametrize("raw,why", [
    ([], "最外層不是物件"),
    ({"aria": {"ok": True}}, "缺類別"),
    ({**snap(), "hosts": {"ok": "yes", "rows": []}}, "ok 不是布林"),
    ({**snap(), "hosts": {"ok": True, "total": 1, "rows": [{"name": "x"}]}}, "沒有 id"),
    ({**snap(), "datastores": {"ok": True, "total": 1, "rows": [{"id": "d", "free_gb": "500"}]}}, "數字是字串"),
    ({**snap(), "datastores": {"ok": True, "total": 1, "rows": [{"id": "d", "free_gb": -1}]}}, "負數"),
    ({**snap(), "datastores": {"ok": True, "total": 1, "rows": [{"id": "d", "hosts": 1.5}]}}, "掛載數不是整數"),
])
def test_parse_拒收壞資料(env, raw, why):
    c, drop = env
    send(drop, "ariadc_bad", raw)
    assert ad.import_pending(c)["rejected"] == 1, why
    assert ad.latest_any(c) is None


def test_快速通道_一般排程不碰_匯入存sha與規則版本(env):
    c, drop = env
    send(drop, "ariadc_20261113T060000", snap())
    assert ri.pending_batches(c) == []                                                                 # 一般排程跳過 ariadc_
    assert ad.import_pending(c)["imported"] == 1
    b = ad.latest_complete(c)
    assert b and b["complete"] == 1 and len(b["file_sha"]) == 64 and b["raw_sha"][0]["name"] == "raw_001_hosts.json"
    assert b["rule_version"] == ad.RULE_VERSION and b["summary"]["datastores"]["dups"] == 1
    assert len(ad.datastores(c, b["batch_id"], VCID)) == 5                                            # d1 重複只存一筆
    assert ad.import_pending(c)["status"] == "idle"


@pytest.mark.parametrize("mut,word", [
    (lambda d: d["hosts"].update(rows=[], total=0), "ESXi 主機 0 筆"),
    (lambda d: d["datastores"].update(rows=[], total=0), "資料存放區 0 筆"),
    (lambda d: d["adapters"].update(rows=[], total=0), "vROps 收集器 0 筆"),
    (lambda d: d["alerts"].update(rows=[], total=0, fetched=0), "警示 0 筆"),
    (lambda d: d["hosts"].update(total=99), "分頁不完整"),
    (lambda d: d["alerts"].update(fetched=3), "分頁不完整"),
    (lambda d: d["datastores"].update(ok=False, error="HTTP 500"), "資料存放區讀取失敗"),
    (lambda d: d.update(aria={"ok": False, "error": "login failed"}), "vROps 登入失敗"),
])
def test_零筆_分頁不完整_類別失敗_都標不完整_每日檢查無資料(env, mut, word):
    c, drop = env
    d = snap()
    mut(d)
    send(drop, "ariadc_x", d)
    ad.import_pending(c)
    b = ad.latest_any(c)
    assert b["complete"] == 0 and any(word in p for p in b["problems"]), b["problems"]
    r, _v, _ = gen(c)
    assert r["VC-1"]["verdict"] == "nodata" and "從沒收到完整" in r["VC-1"]["reason"] and word in r["VC-1"]["reason"]


def test_警示篩完零筆但vROps有active_不是阻擋(env):
    c, drop = env
    send(drop, "ariadc_x", snap(alerts=[]))
    ad.import_pending(c)
    assert ad.latest_complete(c) is not None


def test_manifest筆數對不上_不完整(env):
    c, drop = env
    send(drop, "ariadc_x", snap(), counts={"adapters": 2, "hosts": 4, "datastores": 7, "alerts": 5})
    ad.import_pending(c)
    assert any("manifest" in p for p in ad.latest_any(c)["problems"])
    send(drop, "ariadc_y", snap(), counts=None)
    ad.import_pending(c)
    assert ad.latest_complete(c) is None


# ---------------------------------------------------------------- 判定
def test_一座一列_預填數字_永遠要人勾(env):
    c, drop = env
    send(drop, "ariadc_a", snap())
    ad.import_pending(c)
    r, v, _ = gen(c)
    a, b = r["VC-1"], r["VC-2"]
    assert a["kind"] == "ref" and a["verdict"] == "ref" and not a["fresh"] and a["rule"] == "vmware"
    st = a["stats"]
    assert (st["expected"], st["seen"], st["connected"], st["maint"], st["abnormal"], st["stale_hosts"], st["missing"], st["extra"]) == (4, 4, 2, 1, 1, 0, 1, 1)
    es = states(a, "ESXi")
    assert es["esxi-a04"] == "缺" and es["esxi-a02"] == "異常" and es["esxi-a03"] == "維護"
    assert any(d["presence"] == "多" and d["name"] == "esxi-a09" for d in a["detail"])
    assert b["verdict"] == "ref" and b["stats"]["seen"] == 1 and b["stats"]["ds_low"] == 0
    assert "請看 Error Log／Mail" in a["reason"]
    assert v["counts"]["ok"] == 0                                    # 不會變成系統正常
    assert dc.confirm_fresh_ok(c, v["run"]["id"], "t")["updated"] == 0


def test_資料存放區_本機碟判斷_去重_門檻口徑(env):
    c, drop = env
    send(drop, "ariadc_a", snap())
    ad.import_pending(c)
    r, _v, _ = gen(c)
    st, ds = r["VC-1"]["stats"], states(r["VC-1"], "資料存放區")
    assert ds["SAN-LUN-01"] == "低於門檻" and ds["SAN-LUN-02"] == "正常"       # 600 不 < 600
    assert ds["datastore1"] == "本機碟（排除）"                                  # VMFS＋掛 1 台
    assert ds["datastore1 (2)"] == "低於門檻"                                     # 名稱像本機但掛載數不知道：列出不排除
    assert ds["NFS-01"] == "無剩餘值"
    assert (st["ds_total"], st["ds_low"], st["ds_local"], st["ds_unknown_local"], st["ds_nofree"], st["ds_dup"]) == (5, 2, 1, 1, 1, 0)
    assert st["ds_min_free_gb"] == 20.0
    dc.save_settings(c, {"vm_lun_op": "<="}, today=tnow().date())
    r, _v, _ = gen(c)
    assert states(r["VC-1"], "資料存放區")["SAN-LUN-02"] == "低於門檻"
    dc.save_settings(c, {"vm_lun_unit": "TB", "vm_lun_threshold": 0.5, "vm_lun_op": "<"}, today=tnow().date())
    r, _v, _ = gen(c)
    assert r["VC-1"]["stats"]["ds_low"] == 2                                   # 0.5 TB＝512 GB：500、20 低於


def test_本機碟判斷_純函式():
    pats = dc.DEFAULTS["vm_local_patterns"]
    assert dc.ds_local({"ds_type": "VMFS", "hosts": 1, "name": "datastore1"}, pats)[0] == "local"
    assert dc.ds_local({"ds_type": "VMFS", "hosts": 1, "name": "SAN-X", "local": 1}, pats)[0] == "local"          # vROps 標本機
    k, why = dc.ds_local({"ds_type": "VMFS", "hosts": 1, "name": "SAN-X"}, pats)                               # 掛 1 台但名稱不像本機
    assert k == "unknown" and "名稱不像本機碟" in why
    assert dc.ds_local({"ds_type": "VMFS", "hosts": 3, "name": "datastore1"}, pats)[0] == "shared"     # 名稱像本機但掛 3 台＝共用
    assert dc.ds_local({"ds_type": "NFS", "hosts": 1, "name": "n"}, pats)[0] == "shared"
    k, why = dc.ds_local({"ds_type": "", "hosts": None, "name": "esx01-local"}, pats)
    assert k == "unknown" and "列出不排除" in why and "名稱像本機碟" in why and "照樣檢查門檻" in why
    assert dc.ds_local({"ds_type": "VMFS", "hosts": None, "name": "SAN"}, pats)[0] == "unknown"


def test_警示_等級清單_分列_清單靜音與逾期自動恢復(env):
    c, drop = env
    send(drop, "ariadc_a", snap())
    ad.import_pending(c)
    today = tnow().date()
    dc.save_settings(c, {"vm_alert_mutes": [{"key": "Known noisy", "vcenter": "", "reason": "廠商已知問題", "approver": "主管甲",
                                             "expires": (today + timedelta(days=3)).isoformat()}]}, today=today)
    r, _v, _ = gen(c)
    al = states(r["VC-1"], "警示")
    assert al == {"Host disconnected": "未解", "Datastore low": "vROps 靜音", "VM snapshot old": "已認領", "Known noisy": "清單靜音"}   # INFORMATION 不在清單
    st = r["VC-1"]["stats"]
    assert (st["alerts_open"], st["alerts_suppressed"], st["alerts_muted"], st["alerts_owned"]) == (1, 1, 1, 1)
    # 到期：直接把設定裡的到期日改成昨天（模擬過了期限）→ 自動恢復成未解，快照寫警示
    cfg = json.loads(c.execute("SELECT value FROM app_settings WHERE key = ?", (dc.SETTING,)).fetchone()[0])
    cfg["vm_alert_mutes"][0]["expires"] = (today - timedelta(days=1)).isoformat()
    c.execute("UPDATE app_settings SET value = ? WHERE key = ?", (json.dumps(cfg, ensure_ascii=False), dc.SETTING))
    c.commit()
    r, v, _ = gen(c)
    assert states(r["VC-1"], "警示")["Known noisy"] == "未解" and "自動恢復" in next(d["what"] for d in r["VC-1"]["detail"] if d["name"] == "Known noisy")
    assert any("已自動恢復" in w for w in v["run"]["warnings"])


@pytest.mark.parametrize("mute,word", [
    ({"key": "x", "reason": "", "approver": "甲", "expires": "2099-01-01"}, "理由與核准人"),
    ({"key": "x", "reason": "r", "approver": "", "expires": "2099-01-01"}, "理由與核准人"),
    ({"name": "x", "reason": "r", "approver": "甲", "expires": "2099-01-01"}, "最多到"),
    ({"key": "x", "reason": "r", "approver": "甲", "expires": "下週"}, "YYYY-MM-DD"),
])
def test_靜音清單要期限理由核准人(env, mute, word):
    c, _ = env
    with pytest.raises(ValueError, match=word):
        dc.save_settings(c, {"vm_alert_mutes": [mute]}, today=tnow().date())


def test_收集器停收_批次太舊_vcid未設_應有清單未設_都無資料(env):
    c, drop = env
    now = tnow()
    old = ms(now - timedelta(hours=3))
    ads = [{"id": "ad-1", "name": "vc-a", "vcid": VCID, "last_collected": old}, {"id": "ad-2", "name": "vc-b", "vcid": VCID2, "last_collected": ms(now)}]
    send(drop, "ariadc_a", snap(adapters=ads))
    ad.import_pending(c)
    r, _v, _ = gen(c)
    assert r["VC-1"]["verdict"] == "nodata" and "停收" in r["VC-1"]["reason"]
    assert r["VC-2"]["verdict"] == "ref"
    r, _v, _ = gen(c, now + timedelta(hours=3))
    assert r["VC-1"]["verdict"] == r["VC-2"]["verdict"] == "nodata" and "太舊" in r["VC-2"]["reason"]
    dc.save_settings(c, {"vm_vcenters": [{"name": "x", "vcid": "", "expected": ["a"]}, {"name": "y", "vcid": VCID2, "expected": []}]}, today=now.date())
    r, _v, _ = gen(c)
    assert "vcid" in r["VC-1"]["reason"] and "應有 ESXi 清單" in r["VC-2"]["reason"]


def test_收集器對到兩個_停判(env):
    c, drop = env
    t = ms(tnow())
    ads = [{"id": "ad-1", "name": "vc-a", "vcid": VCID, "last_collected": t}, {"id": "ad-3", "name": "vc-a2", "vcid": VCID, "last_collected": t},
           {"id": "ad-2", "name": "vc-b", "vcid": VCID2, "last_collected": t}]
    send(drop, "ariadc_a", snap(adapters=ads))
    ad.import_pending(c)
    r, _v, _ = gen(c)
    assert r["VC-1"]["verdict"] == "nodata" and "對不上" in r["VC-1"]["reason"]
    dc.save_settings(c, {"vm_vcenters": [{**VCS[0], "adapter_id": "ad-3"}, VCS[1]]}, today=tnow().date())
    assert gen(c)[0]["VC-1"]["verdict"] == "ref"


def test_這座ESXi或資料存放區零筆_無資料(env):
    c, drop = env
    t = ms(tnow())
    send(drop, "ariadc_a", snap(dss=[{"id": "db1", "name": "B", "vcid": VCID2, "type": "VMFS", "free_gb": 1.0, "hosts": 2, "at": t}]))
    ad.import_pending(c)
    r, _v, _ = gen(c)
    assert r["VC-1"]["verdict"] == "nodata" and "資料存放區 0 個" in r["VC-1"]["reason"]


def test_舊的採集時間不算數(env):
    c, drop = env
    now = tnow()
    old = ms(now - timedelta(hours=5))
    hosts = [{"id": "h1", "name": "esxi-a01", "vcid": VCID, "conn": "connected", "maint": "notInMaintenance", "at": old},
             {"id": "hb1", "name": "esxi-b01", "vcid": VCID2, "conn": "connected", "maint": "", "at": ms(now)}]
    send(drop, "ariadc_a", snap(hosts=hosts))
    ad.import_pending(c)
    r, _v, _ = gen(c)
    assert states(r["VC-1"], "ESXi")["esxi-a01"] == "舊值" and r["VC-1"]["stats"]["stale_hosts"] == 1


# ---------------------------------------------------------------- 人工確認、覆寫、版本
def test_已檢視必勾_結果與單號分開_覆寫留痕(env):
    c, drop = env
    send(drop, "ariadc_a", snap())
    ad.import_pending(c)
    r, _v, rid = gen(c)
    vid = r["VC-1"]["id"]
    with pytest.raises(ValueError, match="已檢視"):
        dc.confirm(c, [vid], "ok", None, "tester", {})
    with pytest.raises(ValueError, match="覆寫原因"):
        dc.confirm(c, [vid], "bad", "x", "tester", {"reviewed": True, "esxi_count": "3"})
    dc.confirm(c, [vid], "bad", "一台斷線", "tester", {"reviewed": True, "ticket": "INC-0001", "esxi_count": "3", "override_reason": "a09 是新進主機，不算",
                                                     "lun_free_gb": "20"})
    row = next(x for x in dc.run_view(c, rid)["rows"] if x["id"] == vid)
    ev = row["extra_vals"]
    assert ev["reviewed"] is True and ev["ticket"] == "INC-0001" and row["result"] == "bad"
    assert [(o["field"], o["original"], o["new"], o["reason"], o["by"]) for o in ev["overrides"]] == [("esxi_count", 4, 3, "a09 是新進主機，不算", "tester")]
    assert ev["overrides"][0]["at"]
    conf = [e for e in dc.events(c, rid) if e["action"] == "confirm"][-1]["detail"]
    assert conf["extra"]["override_reason"]
    buf, _name = dc.export_xlsx(c, rid)
    from openpyxl import load_workbook
    txt = " ".join(str(x.value) for x in load_workbook(buf)["每日檢查"]["D"] if x.value)
    assert "ESXi 台數 4→3" in txt and "INC-0001" in txt


def test_無資料列仍可手填退路_不算覆寫(env):
    c, _drop = env
    r, _v, rid = gen(c)
    assert r["VC-1"]["verdict"] == "nodata"
    dc.confirm(c, [r["VC-1"]["id"]], "ok", None, "t", {"reviewed": True, "esxi_count": "20", "lun_free_gb": "800"})
    ev = next(x for x in dc.run_view(c, rid)["rows"] if x["code"] == "VC-1")["extra_vals"]
    assert ev["esxi_count"] == 20 and "overrides" not in ev


def test_沒設定vm_vcenters_照舊人工列(env):
    c, _drop = env
    dc.save_settings(c, {"vm_vcenters": [], "vcenters": ["VC-甲"]}, today=tnow().date())
    r, _v, _ = gen(c)
    assert r["VC-1"]["kind"] == "manual" and r["VC-1"]["rule"] is None


def test_補抓後重新產生新版本_舊版作廢_有人確認就不動(env, monkeypatch):
    c, drop = env
    now = tnow()
    monkeypatch.setattr(dc, "is_working_day", lambda cfg, d: True)
    dc.save_settings(c, {"enabled": True, "notify_stop_time": "23:59"}, today=now.date())
    _r, _v, rid1 = gen(c, now - timedelta(minutes=2))            # 06:00 沒收到 → VC 無資料
    send(drop, "ariadc_0620", snap(now=now), when=(now - timedelta(minutes=1)).replace(tzinfo=TPE))
    ad.import_pending(c)
    assert dc.vm_refresh(c, now) == "regenerated"
    rid2 = dc.latest_run_id(c, now.date().isoformat())
    assert rid2 != rid1 and dc.run_view(c, rid2)["rows"][[x["code"] for x in dc.run_view(c, rid2)["rows"]].index("VC-1")]["verdict"] == "ref"
    v1 = dc.run_view(c, rid1)
    assert v1["run"]["superseded"] and [e for e in dc.events(c, rid1) if e["action"] == "superseded"][0]["detail"]["by_run"] == rid2
    assert any(x["id"] == rid1 and x["superseded"] for x in v1["runs"])
    with pytest.raises(ValueError, match="最新"):
        dc.submit(c, rid1, "t")
    assert dc.vm_refresh(c, now) == "same"
    # 有人確認過：新批次到了也不重產，只提示
    w = dc.run_view(c, rid2)["rows"][0]
    dc.confirm(c, [w["id"]], "ok", None, "t", {"reviewed": True} if w["rule"] == "vmware" else None)
    send(drop, "ariadc_0730", snap(now=now), when=(now - timedelta(seconds=30)).replace(tzinfo=TPE))
    ad.import_pending(c)
    assert dc.vm_refresh(c, now) == "confirmed" and dc.latest_run_id(c, now.date().isoformat()) == rid2
    assert dc.vm_newer(c, dc.run_view(c, rid2)["run"])["batch"] == "ariadc_0730"


def test_tick_匯入後觸發重新產生(env, monkeypatch):
    c, drop = env
    called = []
    monkeypatch.setattr(dc, "vm_refresh", lambda conn, now: called.append(1) or "skip")
    send(drop, "ariadc_a", snap())
    assert ad.tick(c)["refresh"] == "skip" and called
    assert "refresh" not in ad.tick(c)                            # 沒有新批次就不叫


def test_設定改動留紀錄_應有清單正規化(env):
    c, _ = env
    dc.save_settings(c, {"vm_vcenters": [{"name": "甲", "vcid": VCID, "expected": "ESXI-A01.example.test, esxi-a02  10.99.3.11"}]}, today=tnow().date(), by="admin2")
    s = dc.settings(c)
    assert s["vm_vcenters"][0]["expected"] == ["10.99.3.11", "esxi-a01", "esxi-a02"]
    log = dc.setting_log(c)
    assert log[0]["by"] == "admin2" and log[0]["key"] == "vm_vcenters" and log[0]["before"][0]["name"] == "板橋正式（示範）"
    n = len(log)
    dc.save_settings(c, {"cpu": 70}, today=tnow().date(), by="admin2")     # 非 VMware 設定不記
    assert len(dc.setting_log(c)) == n
    with pytest.raises(ValueError):
        dc.save_settings(c, {"vm_lun_unit": "PB"}, today=tnow().date())
    with pytest.raises(ValueError):
        dc.save_settings(c, {"vm_alert_levels": ["HIGH"]}, today=tnow().date())


# ---------------------------------------------------------------- 數字口徑
def test_verify_numbers_VMware不變式(env):
    c, drop = env
    send(drop, "ariadc_a", snap())
    ad.import_pending(c)
    gen(c)
    vn.RESULTS.clear()
    vn.check_daily_check(c)
    vm = [x for x in vn.RESULTS if "VC-" in x["name"]]
    assert len(vm) >= 10 and all(x["ok"] for x in vm), [x for x in vm if not x["ok"]]


def test_verify_numbers_抓得到明細與數字不一致(env):
    c, drop = env
    send(drop, "ariadc_a", snap())
    ad.import_pending(c)
    _r, _v, rid = gen(c)
    row = c.execute("SELECT id, stats_json FROM daily_check_row WHERE run_id = ? AND code = 'VC-1'", (rid,)).fetchone()
    st = json.loads(row["stats_json"])
    st["ds_low"] += 1
    c.execute("UPDATE daily_check_row SET stats_json = ? WHERE id = ?", (json.dumps(st), row["id"]))
    c.commit()
    vn.RESULTS.clear()
    vn.check_daily_check(c)
    assert any(not x["ok"] for x in vn.RESULTS if "VC-1" in x["name"])


# ---------------------------------------------------------------- API
def test_API_vmware狀態與權限(env):
    import api
    from fastapi.testclient import TestClient
    c, drop = env
    send(drop, "ariadc_a", snap())
    ad.import_pending(c)

    def _get_db():
        cc = db.get_connection()
        try:
            yield cc
        finally:
            cc.close()
    api.app.dependency_overrides[api.get_db] = _get_db
    try:
        cl = TestClient(api.app)
        api.app.dependency_overrides[api.require_auth] = lambda: {"username": "admin", "role": "admin"}
        j = cl.get("/api/daily-check/vmware").json()
        assert j["latest_complete"]["batch_id"] == "ariadc_a" and {x["vcid"] for x in j["vcenters"]} == {VCID, VCID2}
        assert j["log"] and j["adapters"][0]["__no"] == 1
        d = cl.get("/api/daily-check").json()
        assert d["vm_pending"] == {"vm_lun_threshold": 600.0, "vm_lun_unit": "GB", "vm_lun_op": "<", "vm_lun_used_pct_max": 90.0}
        r = cl.put("/api/daily-check/settings", json={"patch": {"vm_lun_threshold": 700}})
        assert r.status_code == 200 and dc.setting_log(c)[0]["by"]
    finally:
        api.app.dependency_overrides.clear()


# ---------------------------------------------------------------- 天條：VMware 段沒有任何連 ESXi／vCenter、建帳號、SSH 的路徑
def _code(p: Path) -> str:
    return "\n".join(ln for ln in p.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#"))


def test_天條_後端不連任何主機_不建帳號_不SSH():
    for f in ("aria_dailycheck.py",):
        src = _code(BACK / f)
        src = re.sub(r'"""[\s\S]*?"""', "", src)          # 去掉說明文字（裡面會寫「不連 vCenter」）
        for bad in ("paramiko", "subprocess", "socket", "requests", "urllib", "http.client", "ssh", "useradd", "sudoers", "onboard",
                    "collect_dispatch", "winrm", "Popen", "os.system", "authorized_keys"):
            assert bad not in src.lower(), f"{f} 不該出現 {bad}"
    dcs = re.sub(r'"""[\s\S]*?"""', "", _code(BACK / "daily_check.py"))
    seg = dcs[dcs.index("def vm_source"):dcs.index("def build_rows")]
    for bad in ("paramiko", "subprocess", "ssh", "onboard", "requests", "urllib", "socket"):
        assert bad not in seg.lower(), bad


def test_天條_收集腳本只對vROps做GET_不碰ESXi或vCenter():
    p = ROOT / "AI" / "tools_relay" / "aria_daily_snap.ps1"
    if not p.exists():
        pytest.skip("relay 產出物沒有 AI/")
    sc = p.read_text(encoding="utf-8")
    assert len(re.findall(r"-Method Post", sc)) == 1 and "/auth/token/acquire" in sc.split("-Method Post", 1)[1][:120]
    assert not re.search(r"-Method\s+(Put|Delete|Patch)", sc, re.I)
    for bad in ("ssh", "plink", "putty", "New-LocalUser", "esxcli", "Connect-VIServer", "PowerCLI", "/sdk", "/rest/vcenter", "/api/vcenter",
                "vim25", "Invoke-Expression", "Bypass", "EncodedCommand", "Start-Process", "Register-ScheduledTask", "Out-File", "Set-Content"):
        assert bad.lower() not in sc.lower(), bad
    paths = re.findall(r'"\$B(/[^"?]*)', sc) + re.findall(r"\$B \+ '(/[^'?]*)", sc) + re.findall(r"Get-Paged '(/[^'?]*)", sc)
    assert len(paths) >= 8
    for u in paths:
        assert u.startswith(("/auth/token/acquire", "/adapters", "/resources", "/alerts")), u


# ---------------------------------------------------------------- 審查修正（P1～P3）
def test_P1_無法歸屬vCenter的警示與資料存放區_每一列都標出_不可寫沒有異常(env):
    c, drop = env
    t = ms(tnow())
    hosts = [{"id": "h1", "name": "esxi-a01", "vcid": VCID, "conn": "connected", "maint": "", "at": t},
             {"id": "hb1", "name": "esxi-b01", "vcid": VCID2, "conn": "connected", "maint": "", "at": t}]
    dss = [{"id": "d1", "name": "SAN-A", "vcid": VCID, "type": "VMFS", "free_gb": 5000.0, "hosts": 3, "at": t},
           {"id": "db1", "name": "SAN-B", "vcid": VCID2, "type": "VMFS", "free_gb": 5000.0, "hosts": 3, "at": t},
           {"id": "dx", "name": "ORPHAN-LUN", "vcid": "", "type": "VMFS", "free_gb": 10.0, "hosts": 2, "at": t}]
    als = [{"id": "ax", "res_id": "gone", "res_kind": "", "res_name": "", "vcid": "", "name": "Lost resource alert", "level": "CRITICAL",
            "status": "ACTIVE", "control": "OPEN", "owner": "", "start": t},
           {"id": "ay", "res_id": "r9", "res_kind": "VirtualMachine", "res_name": "vm", "vcid": "vcid-unknown", "name": "Other VC", "level": "WARNING",
            "status": "ACTIVE", "control": "OPEN", "owner": "", "start": t}]
    send(drop, "ariadc_a", snap(hosts=hosts, dss=dss, alerts=als))
    ad.import_pending(c)
    dc.save_settings(c, {"vm_vcenters": [{**VCS[0], "expected": ["esxi-a01"]}, VCS[1]]}, today=tnow().date())
    r, _v, _ = gen(c)
    for code in ("VC-1", "VC-2"):
        st = r[code]["stats"]
        assert (st["unassigned_alerts"], st["unassigned_ds"]) == (2, 1), code
        assert "無法歸屬 vCenter：警示 2 筆、資料存放區 1 個" in r[code]["value_text"]
        assert "沒有異常" not in r[code]["reason"] and "無法歸屬" in r[code]["reason"]
        assert sum(1 for d in r[code]["detail"] if d["state"] == "無法歸屬") == 3
    vn.RESULTS.clear()
    vn.check_daily_check(c)
    assert all(x["ok"] for x in vn.RESULTS if "VC-" in x["name"])


def test_P1_全部歸屬時_才可寫沒有異常(env):
    c, drop = env
    t = ms(tnow())
    hosts = [{"id": "h1", "name": "esxi-a01", "vcid": VCID, "conn": "connected", "maint": "", "at": t},
             {"id": "hb1", "name": "esxi-b01", "vcid": VCID2, "conn": "connected", "maint": "", "at": t}]
    dss = [{"id": "d1", "name": "SAN-A", "vcid": VCID, "type": "VMFS", "free_gb": 5000.0, "hosts": 3, "at": t},
           {"id": "db1", "name": "SAN-B", "vcid": VCID2, "type": "VMFS", "free_gb": 5000.0, "hosts": 3, "at": t}]
    send(drop, "ariadc_a", snap(hosts=hosts, dss=dss, alerts=[]))
    ad.import_pending(c)
    dc.save_settings(c, {"vm_vcenters": [{**VCS[0], "expected": ["esxi-a01"]}, VCS[1]]}, today=tnow().date())
    r, _v, _ = gen(c)
    assert "預填看起來沒有異常" in r["VC-1"]["reason"] and not r["VC-1"]["stats"]["has_issue"]
    # 預填沒異常，勾正常不用說明
    dc.confirm(c, [r["VC-1"]["id"]], "ok", None, "t", {"reviewed": True})


def test_P2_5_掛2台低於門檻_掛1台名稱不像本機_都照樣檢查並列出(env):
    c, drop = env
    t = ms(tnow())
    dss = [{"id": "d1", "name": "SAN-SHARED", "vcid": VCID, "type": "VMFS", "free_gb": 100.0, "hosts": 2, "at": t},
           {"id": "d2", "name": "SAN-SINGLE", "vcid": VCID, "type": "VMFS", "free_gb": 50.0, "hosts": 1, "at": t},
           {"id": "d3", "name": "datastore1", "vcid": VCID, "type": "VMFS", "free_gb": 1.0, "hosts": 1, "at": t},
           {"id": "db1", "name": "B", "vcid": VCID2, "type": "VMFS", "free_gb": 9999.0, "hosts": 2, "at": t}]
    send(drop, "ariadc_a", snap(dss=dss))
    ad.import_pending(c)
    r, _v, _ = gen(c)
    ds = states(r["VC-1"], "資料存放區")
    assert ds == {"SAN-SHARED": "低於門檻", "SAN-SINGLE": "低於門檻", "datastore1": "本機碟（排除）"}
    assert r["VC-1"]["stats"]["ds_unknown_local"] == 1


def test_P2_3_靜音到期上限可調_逾期自動移出並記紀錄(env):
    c, _ = env
    today = tnow().date()
    good = {"name": "A, with comma", "res_id": "", "reason": "r", "approver": "甲", "expires": (today + timedelta(days=30)).isoformat()}
    old = {"name": "Old", "res_id": "x", "reason": "r", "approver": "甲", "expires": (today - timedelta(days=1)).isoformat()}
    dc.save_settings(c, {"vm_alert_mutes": [good, old]}, today=today, by="admin")
    assert [m["name"] for m in dc.settings(c)["vm_alert_mutes"]] == ["A, with comma"]
    assert any(x["key"].startswith("vm_alert_mutes（逾期自動移出）") and x["before"][0]["name"] == "Old" for x in dc.setting_log(c))
    with pytest.raises(ValueError, match="最多到"):
        dc.save_settings(c, {"vm_mute_max_days": 10, "vm_alert_mutes": [good]}, today=today)
    dc.save_settings(c, {"vm_mute_max_days": 120, "vm_alert_mutes": [{**good, "expires": (today + timedelta(days=100)).isoformat()}]}, today=today)


def test_P3_靜音以名稱加資源ID比對(env):
    c, drop = env
    send(drop, "ariadc_a", snap())
    ad.import_pending(c)
    today = tnow().date()
    exp = (today + timedelta(days=3)).isoformat()
    dc.save_settings(c, {"vm_alert_mutes": [{"name": "known noisy", "res_id": "other", "reason": "r", "approver": "甲", "expires": exp}]}, today=today)
    assert states(gen(c)[0]["VC-1"], "警示")["Known noisy"] == "未解"           # 資源 ID 不同：不靜音
    dc.save_settings(c, {"vm_alert_mutes": [{"name": "known noisy", "res_id": "vm2", "reason": "r", "approver": "甲", "expires": exp}]}, today=today)
    assert states(gen(c)[0]["VC-1"], "警示")["Known noisy"] == "清單靜音"


def test_P3_維護模式但不回應_列異常(env):
    c, drop = env
    t = ms(tnow())
    hosts = [{"id": "h1", "name": "esxi-a01", "vcid": VCID, "conn": "notResponding", "maint": "inMaintenance", "at": t},
             {"id": "h2", "name": "esxi-a02", "vcid": VCID, "conn": "disconnected", "maint": "inMaintenance", "at": t},
             {"id": "hb1", "name": "esxi-b01", "vcid": VCID2, "conn": "connected", "maint": "", "at": t}]
    send(drop, "ariadc_a", snap(hosts=hosts))
    ad.import_pending(c)
    es = states(gen(c)[0]["VC-1"], "ESXi")
    assert es["esxi-a01"] == "異常" and es["esxi-a02"] == "維護"


def test_P3_收集器ID跟vcid不一致_停判(env):
    c, drop = env
    send(drop, "ariadc_a", snap())
    ad.import_pending(c)
    dc.save_settings(c, {"vm_vcenters": [{**VCS[0], "adapter_id": "ad-2"}, VCS[1]]}, today=tnow().date())
    r = gen(c)[0]["VC-1"]
    assert r["verdict"] == "nodata" and "屬於另一座 vCenter" in r["reason"]


def test_P3_預填有異常勾正常要說明_更正也要已檢視與覆寫留痕(env):
    c, drop = env
    send(drop, "ariadc_a", snap())
    ad.import_pending(c)
    r, _v, rid = gen(c)
    vid = r["VC-1"]["id"]
    assert r["VC-1"]["stats"]["has_issue"]
    with pytest.raises(ValueError, match="要寫說明"):
        dc.confirm(c, [vid], "ok", None, "t", {"reviewed": True})
    dc.confirm(c, [vid], "ok", "已知維護", "t", {"reviewed": True})
    for row in dc.run_view(c, rid)["rows"]:
        if not row["result"]:
            dc.confirm(c, [row["id"]], "ok", "x", "t", {"reviewed": True} if row["rule"] == "vmware" else None)
    dc.submit(c, rid, "t")
    with pytest.raises(ValueError, match="已檢視"):
        dc.correct(c, vid, "bad", "改判", "t2")
    with pytest.raises(ValueError, match="覆寫原因"):
        dc.correct(c, vid, "bad", "改判", "t2", {"reviewed": True, "esxi_count": "9"})
    dc.correct(c, vid, "bad", "改判", "t2", {"reviewed": True, "esxi_count": "9", "override_reason": "實際看 vCenter 是 9 台", "ticket": "INC-9"})
    row = next(x for x in dc.run_view(c, rid)["rows"] if x["id"] == vid)
    assert row["extra_vals"]["overrides"][0]["new"] == 9 and row["extra_vals"]["ticket"] == "INC-9"
    ev = [e for e in dc.events(c, rid) if e["action"] == "correct"][-1]["detail"]
    assert ev["from_extra"]["reviewed"] is True and ev["extra"]["override_reason"]


def test_P2_4_重新產生前在同一交易再檢查有沒有人確認(env):
    c, drop = env
    _r, _v, rid = gen(c)
    w = dc.run_view(c, rid)["rows"][0]
    dc.confirm(c, [w["id"]], "ok", "x", "t", {"reviewed": True} if w["rule"] == "vmware" else None)
    n = c.execute("SELECT COUNT(*) FROM daily_check_run").fetchone()[0]
    assert dc.generate(c, "x", "vm_refresh", tnow(), only_if_untouched=rid) is None          # 檢查完到寫入之間有人確認了 → 不產生
    assert c.execute("SELECT COUNT(*) FROM daily_check_run").fetchone()[0] == n and not dc.run_view(c, rid)["run"]["superseded"]
    assert dc.generate(c, "x", "vm_refresh", tnow(), only_if_untouched=rid + 99) is None      # 已經不是最新那次 → 不產生


def test_P3_verify抓得到正常卻低於門檻(env):
    c, drop = env
    send(drop, "ariadc_a", snap())
    ad.import_pending(c)
    _r, _v, rid = gen(c)
    row = c.execute("SELECT id, detail_json FROM daily_check_row WHERE run_id = ? AND code = 'VC-1'", (rid,)).fetchone()
    det = json.loads(row["detail_json"])
    d = next(x for x in det if x["state"] == "正常" and x["kind"] == "資料存放區")
    d["free_gb"] = 1.0
    c.execute("UPDATE daily_check_row SET detail_json = ? WHERE id = ?", (json.dumps(det, ensure_ascii=False), row["id"]))
    c.commit()
    vn.RESULTS.clear()
    vn.check_daily_check(c)
    assert any(not x["ok"] and "高於門檻" in x["name"] for x in vn.RESULTS)


def test_P1_只有無法歸屬的警示_也不可寫沒有異常(env):
    c, drop = env
    t = ms(tnow())
    hosts = [{"id": "h1", "name": "esxi-a01", "vcid": VCID, "conn": "connected", "maint": "", "at": t},
             {"id": "hb1", "name": "esxi-b01", "vcid": VCID2, "conn": "connected", "maint": "", "at": t}]
    dss = [{"id": "d1", "name": "SAN-A", "vcid": VCID, "type": "VMFS", "free_gb": 5000.0, "hosts": 3, "at": t},
           {"id": "db1", "name": "SAN-B", "vcid": VCID2, "type": "VMFS", "free_gb": 5000.0, "hosts": 3, "at": t}]
    als = [{"id": "ax", "res_id": "gone", "res_kind": "", "res_name": "", "vcid": "", "name": "Lost", "level": "CRITICAL",
            "status": "ACTIVE", "control": "OPEN", "owner": "", "start": t}]
    send(drop, "ariadc_a", snap(hosts=hosts, dss=dss, alerts=als))
    ad.import_pending(c)
    dc.save_settings(c, {"vm_vcenters": [{**VCS[0], "expected": ["esxi-a01"]}, VCS[1]]}, today=tnow().date())
    r, _v, _ = gen(c)
    assert r["VC-1"]["stats"]["has_issue"] and "沒有異常" not in r["VC-1"]["reason"]
    with pytest.raises(ValueError, match="要寫說明"):
        dc.confirm(c, [r["VC-1"]["id"]], "ok", None, "t", {"reviewed": True})



def test_審查二P2_收集器有但設定沒有的vCenter_算無法歸屬(env):
    c, drop = env
    t = ms(tnow())
    ads = [{"id": "ad-1", "name": "vc-a", "vcid": VCID, "last_collected": t}, {"id": "ad-2", "name": "vc-b", "vcid": VCID2, "last_collected": t},
           {"id": "ad-3", "name": "vc-c.example.test", "vcid": "vcid-cccc", "last_collected": t}]
    hosts = [{"id": "h1", "name": "esxi-a01", "vcid": VCID, "conn": "connected", "maint": "", "at": t},
             {"id": "hb1", "name": "esxi-b01", "vcid": VCID2, "conn": "connected", "maint": "", "at": t}]
    dss = [{"id": "d1", "name": "SAN-A", "vcid": VCID, "type": "VMFS", "free_gb": 5000.0, "hosts": 3, "at": t},
           {"id": "db1", "name": "SAN-B", "vcid": VCID2, "type": "VMFS", "free_gb": 5000.0, "hosts": 3, "at": t},
           {"id": "dc1", "name": "SAN-C", "vcid": "vcid-cccc", "type": "VMFS", "free_gb": 10.0, "hosts": 3, "at": t}]
    als = [{"id": "ac", "res_id": "hc", "res_kind": "HostSystem", "res_name": "esxi-c01", "vcid": "vcid-cccc", "name": "C down", "level": "CRITICAL",
            "status": "ACTIVE", "control": "OPEN", "owner": "", "start": t}]
    send(drop, "ariadc_a", snap(adapters=ads, hosts=hosts, dss=dss, alerts=als))
    ad.import_pending(c)
    dc.save_settings(c, {"vm_vcenters": [{**VCS[0], "expected": ["esxi-a01"]}, VCS[1]]}, today=tnow().date())
    r, _v, _ = gen(c)
    st = r["VC-1"]["stats"]
    assert (st["unassigned_alerts"], st["unassigned_ds"]) == (1, 1) and st["has_issue"] and "沒有異常" not in r["VC-1"]["reason"]
    assert any("設定沒有這座 vCenter" in d["what"] for d in r["VC-1"]["detail"] if d["state"] == "無法歸屬")


def test_審查二P3_改最長天數重新檢查既有靜音(env):
    c, _ = env
    today = tnow().date()
    m = {"name": "N", "res_id": "", "reason": "r", "approver": "甲", "expires": (today + timedelta(days=60)).isoformat()}
    dc.save_settings(c, {"vm_alert_mutes": [m]}, today=today)
    with pytest.raises(ValueError, match="最多到"):
        dc.save_settings(c, {"vm_mute_max_days": 30}, today=today)
    assert dc.settings(c)["vm_mute_max_days"] == 90
    dc.save_settings(c, {"vm_mute_max_days": 60}, today=today)


def test_審查二P3_舊格式vcenter欄位照舊限定範圍(env):
    c, drop = env
    send(drop, "ariadc_a", snap())
    ad.import_pending(c)
    today = tnow().date()
    exp = (today + timedelta(days=3)).isoformat()
    dc.save_settings(c, {"vm_alert_mutes": [{"key": "Known noisy", "vcenter": "別座", "reason": "r", "approver": "甲", "expires": exp}]}, today=today)
    assert dc.settings(c)["vm_alert_mutes"][0]["vcenter"] == "別座"
    assert states(gen(c)[0]["VC-1"], "警示")["Known noisy"] == "未解"
    dc.save_settings(c, {"vm_alert_mutes": [{"key": "Known noisy", "vcenter": VCS[0]["name"], "reason": "r", "approver": "甲", "expires": exp}]}, today=today)
    assert states(gen(c)[0]["VC-1"], "警示")["Known noisy"] == "清單靜音"


def test_審查二P3_排程說明一律帶指紋_沒帶印警告():
    p = ROOT / "AI" / "tools_relay" / "aria_daily_snap.ps1"
    if not p.exists():
        pytest.skip("relay 產出物沒有 AI/")
    sc = p.read_text(encoding="utf-8")
    assert "if (-not $script:pin) { Write-Host 'WARNING: no -CertThumbprint" in sc
    dk = (ROOT / "APP" / "asset-module" / "frontend" / "content" / "devkit.ts").read_text(encoding="utf-8")
    d12 = dk[dk.index("id: 'D12'"):dk.index("text: `# ARIA-DAILYCHECK-SCRIPT")]
    assert d12.count("-CertThumbprint") >= 3 and "aria_daily_snap.ps1 -AriaHost <vROps 位址> -SaveRaw" not in d12


def test_已知例外_vROps資料存放區以名稱對RVTools身分_例外不算低於門檻(env):
    """2026-11-14：1.5 VMware 段跟 5.2 用同一份已知例外；vROps 的資料存放區以名稱（唯一）對 RVTools 卷宗身分。"""
    import json as _json

    import capacity_exempt as ce
    c, drop = env
    rec = {"Name": "SAN-LUN-01", "URL": "ds:///vmfs/volumes/fake01/", "VI SDK Server": "10.99.0.1", "Type": "VMFS",
           "Capacity MiB": 4000 * 1024, "In Use MiB": 3500 * 1024, "Free MiB": 500 * 1024, "# VMs": 1, "# Hosts": 4}
    c.execute("INSERT INTO source_record (source, source_key, payload, resolved_status, collected_at) VALUES "
              "('vcenter_extra:vDatastore', 'x#0', ?, 'extra', datetime('now','localtime'))", (_json.dumps(rec),))
    ce.set_exempt(c, {"kind": "ds", "key": "url:ds:///vmfs/volumes/fake01/", "label": "SAN-LUN-01", "used_pct": 87.5,
                      "free_gb": 500.0, "cap_gb": 4000.0, "source": "rvtools"}, "舊系統資料、不再寫入", 90, "admin")
    c.commit()
    send(drop, "ariadc_a", snap())
    ad.import_pending(c)
    r, _v, _ = gen(c)
    st, ds = r["VC-1"]["stats"], states(r["VC-1"], "資料存放區")
    assert ds["SAN-LUN-01"] == "已知例外" and ds["datastore1 (2)"] == "低於門檻"
    assert (st["ds_low"], st["ds_exempt"], st["ds_total"]) == (1, 1, 5)
